"""Персональная папка серии.

Суть фичи в одной строчке: назначение отдельной серии должно перевешивать общую
папку тайтла — и при одиночной загрузке, и при батче «все серии». Если это
правило нарушится, файлы молча уедут не туда, и заметить это можно будет только
по факту. Поэтому тесты в основном про порядок разрешения.
"""

from __future__ import annotations

import pytest

from sqlalchemy import select

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.library import EpisodeTarget, LibraryFolder, LibraryRoot, TitleMapping
from app.services.download_queue import ensure_target_folder, episode_target_folder

SERIES = 555001


@pytest.fixture
def two_folders(tmp_path):
    """Общая папка тайтла и отдельная папка под одну серию."""
    common_dir = tmp_path / "Тайтл"
    special_dir = tmp_path / "Тайтл — особая серия"
    common_dir.mkdir()
    special_dir.mkdir()

    with session_scope() as session:
        # Тайтл нужен по-настоящему: у title_mapping внешний ключ на series.
        if session.get(Series, SERIES) is None:
            session.add(
                Series(
                    id=SERIES,
                    title="Тестовый тайтл",
                    type="tv",
                    type_title="ТВ сериал",
                    number_of_episodes=12,
                )
            )
            session.flush()

        root = LibraryRoot(
            kind="series", path=str(tmp_path), is_writable=True, is_available=True
        )
        session.add(root)
        session.flush()

        # Серии лежат прямо в папке тайтла и сезонных подпапок нет — значит
        # загрузка идёт к соседям, а не в новую `Season 1`. Здесь проверяется
        # приоритет назначения серии, и раскладка не должна в это вмешиваться.
        common = LibraryFolder(
            root_id=root.id, rel_path="Тайтл", name="Тайтл", video_count=5
        )
        special = LibraryFolder(
            root_id=root.id, rel_path="Тайтл — особая серия", name="особая"
        )
        session.add_all([common, special])
        session.flush()

        session.add(
            TitleMapping(
                series_id=SERIES,
                folder_id=common.id,
                source="manual",
                confidence=1.0,
                is_manual=True,
                is_primary=True,
            )
        )
        ids = {"root": root.id, "common": common.id, "special": special.id}

    yield ids

    with session_scope() as session:
        for row in session.query(EpisodeTarget).filter_by(series_id=SERIES).all():
            session.delete(row)
        for row in session.query(TitleMapping).filter_by(series_id=SERIES).all():
            session.delete(row)
        for folder_id in (ids["common"], ids["special"]):
            folder = session.get(LibraryFolder, folder_id)
            if folder is not None:
                session.delete(folder)
        root = session.get(LibraryRoot, ids["root"])
        if root is not None:
            session.delete(root)
        series = session.get(Series, SERIES)
        if series is not None:
            session.delete(series)


def _assign(episode: float, folder_id: int) -> None:
    with session_scope() as session:
        session.add(
            EpisodeTarget(series_id=SERIES, episode=episode, folder_id=folder_id)
        )


class TestResolutionOrder:
    def test_without_assignment_everything_goes_to_the_common_folder(self, two_folders):
        result = ensure_target_folder(SERIES, episode=3)
        assert result is not None
        assert result[0].id == two_folders["common"]

    def test_assigned_episode_goes_to_its_own_folder(self, two_folders):
        _assign(3, two_folders["special"])
        result = ensure_target_folder(SERIES, episode=3)
        assert result is not None
        assert result[0].id == two_folders["special"]

    def test_other_episodes_are_unaffected(self, two_folders):
        # Ровно то поведение, которое подтвердил пользователь: при батче
        # переназначенная серия уходит в свою папку, остальные — в общую.
        _assign(3, two_folders["special"])
        assert ensure_target_folder(SERIES, episode=2)[0].id == two_folders["common"]
        assert ensure_target_folder(SERIES, episode=4)[0].id == two_folders["common"]

    def test_call_without_episode_number_falls_back_to_common(self, two_folders):
        # Номер серии известен не всегда (фильм, спешл без номера). Отсутствие
        # номера не должно ломать загрузку — просто нет персонального назначения.
        _assign(3, two_folders["special"])
        assert ensure_target_folder(SERIES, episode=None)[0].id == two_folders["common"]


class TestLookup:
    def test_returns_none_when_nothing_assigned(self, two_folders):
        assert episode_target_folder(SERIES, 1) is None

    def test_returns_none_for_missing_episode_number(self, two_folders):
        _assign(1, two_folders["special"])
        assert episode_target_folder(SERIES, None) is None

    def test_fractional_episode_numbers_work(self, two_folders):
        # Спешлы между сериями нумеруются дробно (7.5) — это штатный случай.
        _assign(7.5, two_folders["special"])
        result = episode_target_folder(SERIES, 7.5)
        assert result is not None
        assert result[0].id == two_folders["special"]


class TestFolderOptions:
    """Список папок-кандидатов для формы назначения.

    Главное требование: `Specials` обязана быть в списке. Она привязана к
    другому `series_id`, поэтому в «папки тайтла» не попадает — а это как раз
    самый частый случай, ради которого назначение и делают.
    """

    def test_title_folders_are_offered(self, two_folders, client):
        response = client.get(f"/api/library/episode-targets/{SERIES}/folders")
        assert response.status_code == 200
        paths = {item["rel_path"] for item in response.json()}
        assert "Тайтл" in paths

    def test_siblings_are_offered_too(self, two_folders, client):
        # Соседняя папка под тем же родителем — это и есть Specials в жизни.
        with session_scope() as session:
            root_id = session.execute(
                select(LibraryRoot.id).order_by(LibraryRoot.id.desc()).limit(1)
            ).scalar_one()
            common = session.execute(
                select(LibraryFolder).where(LibraryFolder.rel_path == "Тайтл")
            ).scalar_one()
            session.add(
                LibraryFolder(
                    root_id=root_id,
                    rel_path="Тайтл/Specials",
                    name="Specials",
                    parent_id=common.id,
                    season_number=0,
                    video_count=1,
                )
            )

        paths = {
            item["rel_path"]
            for item in client.get(f"/api/library/episode-targets/{SERIES}/folders").json()
        }
        assert "Тайтл/Specials" in paths

    def test_unmapped_title_gets_empty_list(self, client):
        # Без привязки к папкам предлагать нечего — и это не ошибка.
        assert client.get("/api/library/episode-targets/999777/folders").json() == []


class TestSeparateReset:
    """Сброс отдельной серии и сброс всего тайтла — разные операции."""

    def test_clearing_one_leaves_the_rest(self, two_folders, client):
        for episode in (5, 6, 7):
            client.post(
                "/api/library/episode-targets",
                json={
                    "series_id": SERIES,
                    "episode": episode,
                    "folder_id": two_folders["special"],
                },
            )
        targets = client.get(f"/api/library/episode-targets/{SERIES}").json()["targets"]
        assert len(targets) == 3

        client.delete(f"/api/library/episode-targets/{targets[0]['id']}")
        left = client.get(f"/api/library/episode-targets/{SERIES}").json()["targets"]
        assert [t["episode"] for t in left] == [6, 7]

    def test_clearing_the_title_removes_everything(self, two_folders, client):
        for episode in (5, 6, 7):
            client.post(
                "/api/library/episode-targets",
                json={
                    "series_id": SERIES,
                    "episode": episode,
                    "folder_id": two_folders["special"],
                },
            )

        response = client.delete(f"/api/library/episode-targets/series/{SERIES}")
        assert response.json() == {"deleted": 3}
        assert client.get(f"/api/library/episode-targets/{SERIES}").json()["targets"] == []

    def test_clearing_an_empty_title_is_not_an_error(self, client):
        # Кнопка «снять все» не должна падать, если снимать нечего.
        assert client.delete("/api/library/episode-targets/series/999778").json() == {
            "deleted": 0
        }

    def test_other_titles_are_untouched(self, two_folders, client):
        # Сброс тайтла не должен задеть чужие назначения.
        other = SERIES + 1
        with session_scope() as session:
            if session.get(Series, other) is None:
                session.add(Series(id=other, title="Другой", type="tv"))
            session.flush()
            session.add(
                EpisodeTarget(
                    series_id=other, episode=1, folder_id=two_folders["special"]
                )
            )

        client.post(
            "/api/library/episode-targets",
            json={"series_id": SERIES, "episode": 5, "folder_id": two_folders["special"]},
        )
        client.delete(f"/api/library/episode-targets/series/{SERIES}")

        with session_scope() as session:
            left = session.execute(
                select(EpisodeTarget).where(EpisodeTarget.series_id == other)
            ).scalars().all()
            assert len(left) == 1
