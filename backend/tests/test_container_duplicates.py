"""Папка-контейнер не должна дублировать привязку своей подпапки.

Дубликат возникает закономерно, а не случайно: `<anilistid>` лежит в
`tvshow.nfo` корневой папки и указывает на первый сезон, а `Season 1` своего ID
не имеет и наследует родительский. В итоге один тайтл привязан и к `Show`,
и к `Show/Season 1`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.library import LibraryFolder, LibraryRoot, TitleMapping
from app.services.library_service import _drop_container_duplicates


@pytest.fixture
def tree(tmp_path):
    """Корень с папкой шоу и двумя подпапками сезонов."""
    with session_scope() as session:
        session.query(TitleMapping).delete()
        session.query(LibraryFolder).delete()
        session.query(LibraryRoot).delete()

    # У TitleMapping.series_id внешний ключ — без настоящих строк каталога
    # вставка падает на FOREIGN KEY, а не на проверяемой логике.
    with session_scope() as session:
        for series_id in (111, 222, 260, 3499):
            if session.get(Series, series_id) is None:
                session.add(Series(id=series_id, title=f"Тайтл {series_id}", type="tv"))

    with session_scope() as session:
        root = LibraryRoot(kind="series", path=str(tmp_path), is_available=True)
        session.add(root)
        session.flush()

        show = LibraryFolder(root_id=root.id, rel_path="Show", name="Show", video_count=0)
        session.add(show)
        session.flush()
        s1 = LibraryFolder(
            root_id=root.id, rel_path="Show/Season 1", name="Season 1",
            parent_id=show.id, video_count=12, season_number=1,
        )
        s2 = LibraryFolder(
            root_id=root.id, rel_path="Show/Season 2", name="Season 2",
            parent_id=show.id, video_count=12, season_number=2,
        )
        session.add_all([s1, s2])
        session.flush()
        specials = LibraryFolder(
            root_id=root.id, rel_path="Show/Specials", name="Specials",
            parent_id=show.id, video_count=1, season_number=0,
        )
        session.add(specials)
        session.flush()
        ids = {
            "root": root.id, "show": show.id,
            "s1": s1.id, "s2": s2.id, "specials": specials.id,
        }

    yield ids

    with session_scope() as session:
        session.query(TitleMapping).delete()
        session.query(LibraryFolder).delete()
        session.query(LibraryRoot).delete()


def _map(folder_id: int, series_id: int, manual: bool = False) -> None:
    with session_scope() as session:
        session.add(
            TitleMapping(
                series_id=series_id, folder_id=folder_id, source="anilist_id",
                confidence=100.0, is_manual=manual, is_primary=False,
            )
        )


def _run() -> tuple[int, list[tuple[int, str]]]:
    with session_scope() as session:
        folders = session.execute(select(LibraryFolder)).scalars().all()
        dropped = _drop_container_duplicates(session, list(folders))

    with session_scope() as session:
        rows = session.execute(
            select(TitleMapping.series_id, LibraryFolder.rel_path).join(
                LibraryFolder, TitleMapping.folder_id == LibraryFolder.id
            )
        ).all()
    return dropped, sorted((r.series_id, r.rel_path) for r in rows)


class TestContainerDuplicates:
    def test_container_loses_to_its_season_folder(self, tree):
        # Ровно случай со скриншота: 260 привязан и к «Show», и к «Show/Season 1».
        _map(tree["show"], 260)
        _map(tree["s1"], 260)
        dropped, rows = _run()
        assert dropped == 1
        assert rows == [(260, "Show/Season 1")]

    def test_other_seasons_are_untouched(self, tree):
        _map(tree["show"], 260)
        _map(tree["s1"], 260)
        _map(tree["s2"], 3499)
        dropped, rows = _run()
        assert dropped == 1
        assert rows == [(260, "Show/Season 1"), (3499, "Show/Season 2")]

    def test_empty_show_folder_keeps_its_mapping(self, tree):
        # ⚠️ Отдельное требование: папка с одним tvshow.nfo и без видео должна
        # мапиться и показывать 0 серий. Сплошное «нет видео → не мапить» его бы
        # нарушило, поэтому снимаем только при наличии потомка с тем же тайтлом.
        _map(tree["show"], 260)
        _map(tree["s2"], 3499)
        dropped, rows = _run()
        assert dropped == 0
        assert (260, "Show") in rows

    def test_manual_mapping_is_never_dropped(self, tree):
        # Выбор пользователя автоскан не пересматривает.
        _map(tree["show"], 260, manual=True)
        _map(tree["s1"], 260)
        dropped, rows = _run()
        assert dropped == 0
        assert (260, "Show") in rows

    def test_unrelated_series_in_container_survives(self, tree):
        # У контейнера свой тайтл, у подпапки свой — дубликата нет.
        _map(tree["show"], 111)
        _map(tree["s1"], 222)
        dropped, rows = _run()
        assert dropped == 0
        assert len(rows) == 2

    def test_deep_nesting(self, tree):
        # Внук тоже считается потомком: «Show» против «Show/Season 1/Part 2».
        with session_scope() as session:
            deep = LibraryFolder(
                root_id=tree["root"], rel_path="Show/Season 1/Part 2", name="Part 2",
                parent_id=tree["s1"], video_count=6,
            )
            session.add(deep)
            session.flush()
            deep_id = deep.id
        _map(tree["show"], 260)
        _map(deep_id, 260)
        dropped, rows = _run()
        assert dropped == 1
        assert rows == [(260, "Show/Season 1/Part 2")]

    def test_single_mapping_is_left_alone(self, tree):
        _map(tree["s1"], 260)
        dropped, rows = _run()
        assert dropped == 0
        assert rows == [(260, "Show/Season 1")]


class TestShapesUserAskedAbout:
    """Раскладки, которые встречаются в живых библиотеках.

    Правило снимает привязку с корня, поэтому ошибиться здесь дороже, чем
    оставить дубликат: дубликат виден в интерфейсе, а файлы, уехавшие не в ту
    папку, — нет.
    """

    def test_only_specials_under_the_container(self, tree):
        # Отдельного правила для спешлов нет — решение пользователя. Корень без
        # видео уступает и папке спешлов; загрузки пойдут туда, пока не появится
        # папка сезона с файлами. Раскладка редкая и самоисправляется.
        _map(tree["show"], 260)
        _map(tree["specials"], 260)
        dropped, rows = _run()
        assert dropped == 1
        assert rows == [(260, "Show/Specials")]

    def test_specials_alongside_a_real_season(self, tree):
        # Снимается только привязка корня; подпапки между собой не конкурируют —
        # ни одна не является предком другой. Какая из них станет основной,
        # решает ranking по вкладу в тайтл.
        _map(tree["show"], 260)
        _map(tree["s1"], 260)
        _map(tree["specials"], 260)
        dropped, rows = _run()
        assert dropped == 1
        assert (260, "Show") not in rows
        assert (260, "Show/Season 1") in rows
        assert (260, "Show/Specials") in rows

    def test_videos_lie_directly_in_the_title_folder(self, tree):
        # Подпапок нет вовсе: серии прямо в папке тайтла. Трогать нечего.
        with session_scope() as session:
            folder = session.get(LibraryFolder, tree["show"])
            folder.video_count = 12
        _map(tree["show"], 260)
        dropped, rows = _run()
        assert dropped == 0
        assert rows == [(260, "Show")]

    def test_container_with_its_own_videos_is_not_a_container(self, tree):
        # Серии есть и в корне, и в подпапке. Корень — рабочая папка, привязку
        # с него не снимаем; какая основная, решит ranking по вкладу.
        with session_scope() as session:
            folder = session.get(LibraryFolder, tree["show"])
            folder.video_count = 5
        _map(tree["show"], 260)
        _map(tree["s1"], 260)
        dropped, rows = _run()
        assert dropped == 0
        assert len(rows) == 2

    def test_empty_descendant_does_not_win(self, tree):
        # Пустая подпапка не лучше корня ничем.
        with session_scope() as session:
            session.get(LibraryFolder, tree["s1"]).video_count = 0
        _map(tree["show"], 260)
        _map(tree["s1"], 260)
        dropped, rows = _run()
        assert dropped == 0
        assert len(rows) == 2


class TestStatsMatchTheList:
    """Счётчики и список обязаны говорить одно и то же.

    Расхождение уже случалось: правило про папки-контейнеры добавили в список
    нераспознанного, а `/status` и отчёт остались считать по-старому — и
    статистика показывала «без привязки: 1» при пустом разделе. Заметил
    пользователь, а не тест, поэтому тест теперь есть.
    """

    def _numbers(self, client):
        status = client.get("/api/library/status").json()
        report = client.get("/api/library/mapping-report").json()
        listed = client.get("/api/library/unmapped?only_with_video=false").json()
        return status, report, len(listed)

    def test_container_is_absent_everywhere(self, tree, client):
        # Корень без видео уступил подпапке — он не «ждёт привязки».
        # Остальные подпапки тоже привязываем: непривязанными они были бы
        # по-настоящему, и тест мерил бы не то.
        _map(tree["s1"], 260)
        _map(tree["s2"], 3499)
        _map(tree["specials"], 111)
        status, report, listed = self._numbers(client)
        assert listed == 0
        assert status["folders_unmapped"] == 0
        assert report["folders_unmapped"] == 0
        # И из общего числа он тоже вычтен, иначе «привязано 6 из 7» при
        # пустом списке.
        assert status["folders_total"] == status["folders_mapped"]
        assert report["folders_total"] == report["folders_mapped"]

    def test_really_unmapped_folder_is_still_counted(self, tree, client):
        # Обратная проверка: вычет контейнеров не должен прятать настоящую
        # непривязанную папку — иначе мы починили бы отчёт ценой его пользы.
        _map(tree["s1"], 260)
        _map(tree["s2"], 3499)
        _map(tree["specials"], 111)
        with session_scope() as session:
            root_id = session.execute(
                select(LibraryRoot.id).order_by(LibraryRoot.id.desc()).limit(1)
            ).scalar_one()
            session.add(
                LibraryFolder(
                    root_id=root_id, rel_path="Чужой тайтл", name="Чужой тайтл", video_count=12
                )
            )
        status, report, listed = self._numbers(client)
        assert listed == 1
        assert status["folders_unmapped"] == 1
        assert report["folders_unmapped"] == 1


class TestFlatLayouts:
    """Раскладки без подпапок — их пользователь назвал отдельно."""

    def test_episodes_directly_in_the_title_folder(self, tree, client):
        # Серии лежат прямо в папке тайтла, подпапок нет. Папка привязана,
        # значит нигде не «без привязки».
        with session_scope() as session:
            for folder_id in (tree["s1"], tree["s2"], tree["specials"]):
                session.query(TitleMapping).filter_by(folder_id=folder_id).delete()
                session.delete(session.get(LibraryFolder, folder_id))
            session.get(LibraryFolder, tree["show"]).video_count = 12
        _map(tree["show"], 260)

        status = client.get("/api/library/status").json()
        listed = client.get("/api/library/unmapped?only_with_video=false").json()
        assert listed == []
        assert status["folders_total"] == 1
        assert status["folders_mapped"] == 1
        assert status["folders_unmapped"] == 0

    def test_flat_folder_without_mapping_is_reported(self, tree, client):
        # Та же раскладка, но привязки нет: папка обязана попасть в отчёт —
        # у неё есть видео, и контейнером она не является.
        with session_scope() as session:
            for folder_id in (tree["s1"], tree["s2"], tree["specials"]):
                session.delete(session.get(LibraryFolder, folder_id))
            session.get(LibraryFolder, tree["show"]).video_count = 12

        status = client.get("/api/library/status").json()
        listed = client.get("/api/library/unmapped?only_with_video=false").json()
        assert len(listed) == 1
        assert status["folders_unmapped"] == 1

    def test_empty_folder_without_children_is_reported(self, tree, client):
        # Папка без видео И без подпапок — не контейнер, а просто пустая
        # (в библиотеке встречаются папки с одним tvshow.nfo). Прятать её
        # нельзя: именно так выглядит тайтл, который не смогли опознать.
        with session_scope() as session:
            for folder_id in (tree["s1"], tree["s2"], tree["specials"]):
                session.delete(session.get(LibraryFolder, folder_id))

        listed = client.get("/api/library/unmapped?only_with_video=false").json()
        assert len(listed) == 1
        assert client.get("/api/library/status").json()["folders_unmapped"] == 1


class TestDiskSpaceInStatus:
    """Свободное место на носителях библиотек.

    Опасение из планов — «как узнать место, если папки монтируются отдельно» —
    снимается само: `shutil.disk_usage` спрашивает файловую систему по пути, и
    каждое монтирование отвечает за себя.
    """

    def test_status_reports_free_space(self, tree, client):
        roots = client.get("/api/library/status").json()["roots"]
        assert roots, "тест бессмысленен без корней"
        for root in roots:
            assert root["disk_total_bytes"] is not None
            assert root["disk_free_bytes"] is not None
            # Свободного не может быть больше, чем всего.
            assert 0 <= root["disk_free_bytes"] <= root["disk_total_bytes"]

    def test_roots_on_one_volume_share_the_mount_key(self, tree, client):
        """Корни в одной временной папке лежат на одном носителе.

        Ключ монтирования нужен клиенту, чтобы не сложить одно и то же
        свободное место дважды и не пообещать вдвое больше, чем есть.
        """
        roots = client.get("/api/library/status").json()["roots"]
        mounts = {root["mount"] for root in roots}
        assert mounts == {roots[0]["mount"]}
        assert roots[0]["mount"] is not None

    def test_unavailable_root_does_not_break_status(self, tree, client):
        """Отвалившийся диск не должен ронять ответ.

        Статус библиотеки нужен как раз тогда, когда что-то отвалилось.
        """
        with session_scope() as session:
            root = session.execute(select(LibraryRoot)).scalars().first()
            root.path = "/nope/no/such/path"

        payload = client.get("/api/library/status").json()
        broken = [r for r in payload["roots"] if r["path"] == "/nope/no/such/path"]
        assert broken and broken[0]["disk_free_bytes"] is None
