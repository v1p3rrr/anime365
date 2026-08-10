"""Загрузки раскладываются по сезонным подпапкам.

Решение пользователя: у нового тайтла заводим `Season 1`, у спешлов —
`Specials`, а если рядом уже есть `Season 2`, но нет `Season 1`, то создаём и
её. Так раскладывают Sonarr и Jellyfin, и так уже лежит бо́льшая часть
библиотеки.

⚠️ Одно исключение проверяется отдельно и оно важнее остальных: если серии
лежат **прямо в папке шоу** и сезонных подпапок нет, новая подпапка разорвала
бы сезон на два места. Тогда кладём к соседям.
"""

from __future__ import annotations

import pathlib

import pytest

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.library import LibraryFolder, LibraryRoot, TitleMapping
from app.services.download_queue import desired_season_folder, ensure_target_folder

SERIES = 940_001
SEQUEL = 940_002
SPECIAL = 940_003


@pytest.fixture
def library(tmp_path):
    """Пустой корень сериалов и три тайтла: сезон 1, сезон 3 и спешл."""
    with session_scope() as session:
        session.merge(
            Series(id=SERIES, title="Grand Blue", titles={"romaji": "Grand Blue"},
                   type="tv", number_of_episodes=12)
        )
        session.merge(
            Series(id=SEQUEL, title="Grand Blue 3",
                   titles={"romaji": "Grand Blue Season 3"}, type="tv",
                   number_of_episodes=12)
        )
        session.merge(
            Series(id=SPECIAL, title="Grand Blue OVA",
                   titles={"romaji": "Grand Blue OVA"}, type="ova",
                   number_of_episodes=1)
        )
        root = LibraryRoot(
            kind="series", path=str(tmp_path), is_writable=True, is_available=True
        )
        session.add(root)
        session.flush()
        root_id = root.id

    yield tmp_path, root_id

    with session_scope() as session:
        session.query(TitleMapping).filter(
            TitleMapping.series_id.in_([SERIES, SEQUEL, SPECIAL])
        ).delete(synchronize_session=False)
        session.query(LibraryFolder).filter(LibraryFolder.root_id == root_id).delete(
            synchronize_session=False
        )
        session.query(LibraryRoot).filter(LibraryRoot.id == root_id).delete(
            synchronize_session=False
        )


def _bind(root_id: int, rel_path: str, series_id: int, *, videos: int = 0,
          season: int | None = None, parent_id: int | None = None) -> int:
    with session_scope() as session:
        folder = LibraryFolder(
            root_id=root_id,
            parent_id=parent_id,
            rel_path=rel_path,
            name=rel_path.rsplit("/", 1)[-1],
            season_number=season,
            video_count=videos,
        )
        session.add(folder)
        session.flush()
        session.add(
            TitleMapping(
                series_id=series_id, folder_id=folder.id, source="manual",
                confidence=100.0, is_manual=True, is_primary=True,
            )
        )
        return folder.id


class TestWhichSubfolderIsWanted:
    def test_a_plain_title_wants_season_one(self, library) -> None:
        assert desired_season_folder(SERIES) == "Season 1"

    def test_the_number_comes_from_the_title(self, library) -> None:
        """В Anime365 каждый сезон — отдельный тайтл, номер написан в названии."""
        assert desired_season_folder(SEQUEL) == "Season 3"

    def test_an_ova_wants_specials(self, library) -> None:
        assert desired_season_folder(SPECIAL) == "Specials"


class TestNewTitle:
    def test_a_folder_created_from_scratch_gets_its_season(self, library) -> None:
        tmp_path, _ = library

        folder, root = ensure_target_folder(SERIES, episode=1)
        del tmp_path

        assert folder.rel_path == "Grand Blue/Season 1"
        assert (pathlib.Path(root.path) / folder.rel_path).is_dir()

    def test_a_special_lands_in_specials(self, library) -> None:
        tmp_path, _ = library

        folder, root = ensure_target_folder(SPECIAL, episode=1)
        del tmp_path

        assert folder.rel_path.endswith("/Specials")
        assert (pathlib.Path(root.path) / folder.rel_path).is_dir()


class TestExistingTitle:
    def test_a_missing_season_is_created_next_to_the_others(self, library) -> None:
        """Есть `Season 2`, нет `Season 1` — заводим её, а не сваливаем в корень."""
        tmp_path, root_id = library
        (tmp_path / "Grand Blue" / "Season 2").mkdir(parents=True)
        show = _bind(root_id, "Grand Blue", SERIES)
        _bind(root_id, "Grand Blue/Season 2", SEQUEL, videos=12, season=2, parent_id=show)

        folder, root = ensure_target_folder(SERIES, episode=1)
        del tmp_path

        assert folder.rel_path == "Grand Blue/Season 1"
        assert (pathlib.Path(root.path) / folder.rel_path).is_dir()

    def test_an_existing_season_folder_is_used_as_is(self, library) -> None:
        tmp_path, root_id = library
        (tmp_path / "Grand Blue" / "Season 1").mkdir(parents=True)
        show = _bind(root_id, "Grand Blue", SEQUEL)
        wanted = _bind(
            root_id, "Grand Blue/Season 1", SERIES, videos=5, season=1, parent_id=show
        )
        del show

        folder, _root = ensure_target_folder(SERIES, episode=2)

        assert folder.id == wanted

    def test_loose_episodes_keep_their_neighbours(self, library) -> None:
        """Тот самый случай `Grand Blue Dreaming`: серии лежат в корне папки шоу.

        Завести подпапку значило бы разорвать сезон: три файла остались бы
        снаружи. Такие двойники нам уже дорого обошлись, поэтому кладём рядом.
        """
        tmp_path, root_id = library
        (tmp_path / "Grand Blue Dreaming").mkdir(parents=True)
        loose = _bind(root_id, "Grand Blue Dreaming", SERIES, videos=3)

        folder, _root = ensure_target_folder(SERIES, episode=4)

        assert folder.id == loose
        assert not (tmp_path / "Grand Blue Dreaming" / "Season 1").exists()
