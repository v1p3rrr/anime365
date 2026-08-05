"""Фильм из нескольких частей, разложенный по отдельным папкам.

Живой случай Girls Band Cry: в каталоге есть вхождение «Girls Band Cry Movie»
с **двумя** сериями (37513) и отдельное «Girls Band Cry Movie 2» с одной
(40626). На диске — две папки по одному видео:

    Girls Band Cry Movie 1 - Youth Rhapsody 青春狂走曲/…mkv
    Girls Band Cry Movie 2 - Hey, Our Future  なぁ、未来。/…mkv

Каждое видео единственное в своей папке, поэтому обе части претендуют на первую
серию. Куда какая — зависит от того, на какой тайтл смапилась папка.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select

from app.core.db import Base, engine, session_scope
from app.models.catalog import Series
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, TitleMapping
from app.services.library_scan import scan_root
from app.services.library_service import _map_episodes, _movie_part_number, _persist_root

TWO_PARTS = 960001  # «Girls Band Cry Movie», 2 серии
STANDALONE = 960002  # «Girls Band Cry Movie 2», 1 серия

FOLDERS = {
    "Girls Band Cry Movie 1 - Youth Rhapsody 青春狂走曲": (
        "Girls Band Cry The Movie Part 1 - Youth Rhapsody (2025) - [Bluray-1080p] [x264].mkv"
    ),
    "Girls Band Cry Movie 2 - Hey, Our Future  なぁ、未来。": (
        "GIRLS BAND CRY The Movie - Hey, Our Future (2025) - [Bluray-1080p] [x264].mkv"
    ),
}


def _seed() -> None:
    Base.metadata.create_all(engine)
    with session_scope() as session:
        session.execute(
            delete(EpisodeMapping).where(EpisodeMapping.series_id.in_([TWO_PARTS, STANDALONE]))
        )
        for series_id, title, episodes in (
            (TWO_PARTS, "Girls Band Cry Movie", 2),
            (STANDALONE, "Girls Band Cry Movie 2", 1),
        ):
            existing = session.get(Series, series_id)
            if existing is None:
                session.add(
                    Series(id=series_id, title=title, type="movie", number_of_episodes=episodes)
                )
            else:
                existing.number_of_episodes = episodes


def _build(root: Path) -> int:
    for folder_name, filename in FOLDERS.items():
        folder = root / folder_name
        folder.mkdir(parents=True)
        (folder / filename).write_bytes(b"x" * 10)
    return _persist_root(scan_root(str(root), "movie"))


def _bind(root_id: int, folder_name: str, series_id: int) -> None:
    with session_scope() as session:
        folder_id = session.execute(
            select(LibraryFolder.id).where(
                LibraryFolder.root_id == root_id, LibraryFolder.name == folder_name
            )
        ).scalar_one()
        session.execute(delete(TitleMapping).where(TitleMapping.folder_id == folder_id))
        session.add(
            TitleMapping(
                series_id=series_id, folder_id=folder_id, source="exact_title", confidence=1.0
            )
        )


def _episodes(series_id: int) -> dict[str, float]:
    with session_scope() as session:
        rows = session.execute(
            select(LibraryFile.filename, EpisodeMapping.episode)
            .join(LibraryFile, EpisodeMapping.file_id == LibraryFile.id)
            .where(EpisodeMapping.series_id == series_id)
        ).all()
    return {name: episode for name, episode in rows}


class TestPartNumber:
    def test_reads_the_shapes_that_actually_occur(self) -> None:
        assert _movie_part_number("Girls Band Cry Movie 2 - Hey, Our Future") == 2
        assert _movie_part_number("Some Title The Movie Part 3") == 3
        assert _movie_part_number("Some Title (2025) - 2") == 2  # так называем папки мы сами
        assert _movie_part_number("Girls Band Cry Movie 1 - Youth Rhapsody") == 1

    def test_ignores_names_without_a_part(self) -> None:
        assert _movie_part_number("A Silent Voice") is None
        assert _movie_part_number("Neon Genesis Evangelion - The End of Evangelion") is None


class TestPartsOfOneEntry:
    def test_two_folders_become_two_episodes(self, tmp_path: Path) -> None:
        _seed()
        root_id = _build(tmp_path / "movies-two-parts")
        for folder_name in FOLDERS:
            _bind(root_id, folder_name, TWO_PARTS)

        _map_episodes(root_id)

        assert sorted(_episodes(TWO_PARTS).values()) == [1.0, 2.0]

    def test_standalone_entry_keeps_first_episode(self, tmp_path: Path) -> None:
        """Та же папка «Movie 2», но тайтл с единственной серией — номер 1."""
        _seed()
        root_id = _build(tmp_path / "movies-standalone")
        second = "Girls Band Cry Movie 2 - Hey, Our Future  なぁ、未来。"
        _bind(root_id, second, STANDALONE)

        _map_episodes(root_id)

        assert list(_episodes(STANDALONE).values()) == [1.0]
