"""Что делает скан, когда данные с диска приходят неполными или кривыми.

Три случая, найденные ревью. Общее у них одно: неудача **одной** папки не имеет
права уносить с собой ни соседние папки, ни уже накопленное.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.db import session_scope
from app.models.library import LibraryFile, LibraryFolder
from app.services.library_scan import scan_root
from app.services.library_service import _persist_root, build_index, _map_folders
from app.services.matcher import season_label_from_premiered
from app.services.nfo_parser import NfoData, candidate_titles

from tests.test_mapping_matrix import (
    SHOW,
    TVSHOW_NFO,
    _episodes,
    _mapping,
    _run,
    _season_nfo,
    _seed_catalog,
    _write,
)


class TestUnreadableFolderKeepsItsFiles:
    """Папка с видео отвалилась — её содержимое остаётся в базе.

    ⚠️ Клетка соседняя с `test_unreadable_subtree_keeps_its_data`, и разница
    между ними — вся суть находки. Там нечитаемым становился **родитель**, а
    подпапки выпадали из результата целиком, поэтому до их файлов дело не
    доходило и оговорка в `_forget_vanished_folders` срабатывала.

    Здесь отваливается сама папка с видео. Строку папки та оговорка сберегала
    по-прежнему, а файлы внутри удалялись раньше — как «исчезнувшие», — и по
    каскаду уносили связи серий. Следующий удачный скан вернул бы только
    автоматические: ручная связка и ручной язык файла восстановлению не
    подлежат.
    """

    def _library(self, tmp_path: Path) -> tuple[Path, int]:
        root = tmp_path / "unreadable"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        season = title / "Season 1"
        _write(season, "season.nfo", _season_nfo(1, "2019-01-10"))
        for number in (1, 2):
            _write(season, f"Kagerou Days S01E0{number} [Group] WEBDL-1080p.mkv")
        root_id = _run(root, "series")
        assert _episodes(SHOW) == {1.0, 2.0}
        return root, root_id

    def _fail_the_season(self, root: Path):
        """Так выглядит проход, где открыть удалось родителя, но не подпапку."""
        result = scan_root(str(root), "series")
        child = result.folders[0].children[0]
        assert child.rel_path == "Kagerou Days/Season 1"
        child.files = []
        child.children = []
        child.read_failed = True
        return result

    def test_files_survive(self, tmp_path: Path) -> None:
        root, root_id = self._library(tmp_path)
        _persist_root(self._fail_the_season(root))

        with session_scope() as session:
            names = set(
                session.execute(
                    select(LibraryFile.filename)
                    .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
                    .where(
                        LibraryFolder.root_id == root_id,
                        LibraryFolder.rel_path == "Kagerou Days/Season 1",
                    )
                )
                .scalars()
                .all()
            )
        assert "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv" in names
        assert "Kagerou Days S01E02 [Group] WEBDL-1080p.mkv" in names

    def test_episode_links_survive(self, tmp_path: Path) -> None:
        root, _ = self._library(tmp_path)
        _persist_root(self._fail_the_season(root))
        assert _episodes(SHOW) == {1.0, 2.0}

    def test_folder_row_survives(self, tmp_path: Path) -> None:
        root, root_id = self._library(tmp_path)
        _persist_root(self._fail_the_season(root))

        with session_scope() as session:
            paths = set(
                session.execute(
                    select(LibraryFolder.rel_path).where(LibraryFolder.root_id == root_id)
                )
                .scalars()
                .all()
            )
        assert "Kagerou Days/Season 1" in paths

    def test_recovers_after_a_good_scan(self, tmp_path: Path) -> None:
        # Обратная сторона: сбой ничего не заморозил — обычный проход снова
        # перечитывает папку и обновляет её как всегда.
        root, root_id = self._library(tmp_path)
        _persist_root(self._fail_the_season(root))
        _persist_root(scan_root(str(root), "series"))

        with session_scope() as session:
            folder = session.execute(
                select(LibraryFolder).where(
                    LibraryFolder.root_id == root_id,
                    LibraryFolder.rel_path == "Kagerou Days/Season 1",
                )
            ).scalar_one()
            assert folder.video_count == 2
            assert folder.nfo is not None
        assert _episodes(SHOW) == {1.0, 2.0}


class TestPartialPremieredDate:
    """`<premiered>` с месяцем `00` не роняет сопоставление.

    Частичные даты отдают внешние базы, когда известен только год. Дальше месяц
    уходил в словарь «месяц → сезон» напрямую, и `KeyError` вылетал из
    `matcher.match()` наружу: падал весь скан, а не одна папка.
    """

    @pytest.mark.parametrize("premiered", ["2019-00-00", "2019-13-01", "2019-99-01"])
    def test_month_outside_the_year_is_no_date(self, premiered: str) -> None:
        assert NfoData(kind="season", path="x", premiered=premiered).season_quarter is None

    @pytest.mark.parametrize("premiered", ["2019-01-10", "2019-12-31"])
    def test_real_dates_still_parse(self, premiered: str) -> None:
        quarter = NfoData(kind="season", path="x", premiered=premiered).season_quarter
        assert quarter is not None
        # Проверка обратной подстановкой: раз квартал есть, метка строится.
        assert season_label_from_premiered(*quarter)

    def test_scan_survives_such_a_folder(self, tmp_path: Path) -> None:
        root = tmp_path / "partial"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        season = title / "Season 1"
        _write(season, "season.nfo", _season_nfo(1, "2019-00-00"))
        _write(season, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")

        _seed_catalog()
        root_id = _persist_root(scan_root(str(root), "series"))
        # Именно здесь и вылетал `KeyError`.
        _map_folders(root_id, build_index(), "series")
        assert _mapping(root_id)["Kagerou Days/Season 1"] == SHOW


class TestCandidateTitles:
    def test_the_same_title_padded_differently_is_one_variant(self) -> None:
        # Сравнивалась исходная строка, а добавлялась обрезанная — из-за чего
        # имя папки с пробелами дублировало название из `.nfo`.
        nfo = NfoData(kind="tvshow", path="x", title="Yuru Yuri")
        assert candidate_titles(nfo, "  Yuru Yuri  ") == ["Yuru Yuri"]

    def test_different_titles_are_all_kept(self) -> None:
        nfo = NfoData(kind="tvshow", path="x", title="Yuru Yuri", original_title="ゆるゆり")
        assert candidate_titles(nfo, "YuruYuri - Happy Go Lily") == [
            "Yuru Yuri",
            "ゆるゆり",
            "YuruYuri - Happy Go Lily",
        ]


class TestUnreadableFolderSeenForTheFirstTime:
    """Папка не открылась, и записи о ней в базе ещё нет.

    ⚠️ Найдено ревизией собственной правки: ветка `read_failed` выходит рано и
    выставляет только имя с номером сезона. Для **новой** строки это значит, что
    остальные колонки берут значения по умолчанию, — и если бы обязательная
    колонка осталась без значения, вставка упала бы прямо в скане.

    Раньше это было проверено чтением модели, а не прогоном. Теперь прогоном.
    """

    def test_a_row_appears_and_the_scan_survives(self, tmp_path: Path) -> None:
        root = tmp_path / "first-sight"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        season = title / "Season 1"
        _write(season, "season.nfo", _season_nfo(1, "2019-01-10"))
        _write(season, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")

        result = scan_root(str(root), "series")
        child = result.folders[0].children[0]
        child.files = []
        child.children = []
        child.read_failed = True

        # Базы для этой папки ещё нет: скан идёт впервые.
        root_id = _persist_root(result)

        with session_scope() as session:
            folder = session.execute(
                select(LibraryFolder).where(
                    LibraryFolder.root_id == root_id,
                    LibraryFolder.rel_path == "Kagerou Days/Season 1",
                )
            ).scalar_one()
            assert folder.name == "Season 1", "имя известно из обхода каталога родителя"
            assert folder.season_number == 1
            # Остальное неизвестно — и это честные значения по умолчанию.
            assert folder.video_count == 0
            assert folder.size_bytes == 0
            assert folder.nfo is None

    def test_the_next_good_scan_fills_it_in(self, tmp_path: Path) -> None:
        # Обратная сторона: первая неудача ничего не заморозила.
        root = tmp_path / "first-sight-then-ok"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        season = title / "Season 1"
        _write(season, "season.nfo", _season_nfo(1, "2019-01-10"))
        _write(season, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")

        broken = scan_root(str(root), "series")
        child = broken.folders[0].children[0]
        child.files = []
        child.children = []
        child.read_failed = True
        root_id = _persist_root(broken)

        _persist_root(scan_root(str(root), "series"))

        with session_scope() as session:
            folder = session.execute(
                select(LibraryFolder).where(
                    LibraryFolder.root_id == root_id,
                    LibraryFolder.rel_path == "Kagerou Days/Season 1",
                )
            ).scalar_one()
            assert folder.video_count == 1
            assert folder.nfo is not None

