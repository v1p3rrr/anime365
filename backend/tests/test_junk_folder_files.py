"""Файл со своим `t{id}` опознаётся сам, а не по папке вокруг.

Живой случай: в корне фильмов лежит папка `Bang Dream!`, в которую сложены
**восемь разных фильмов** — «Poppin' Dream!», две части «Episode of Roselia»,
«FILM LIVE» и прочие. В модели «папка → тайтл» одна папка на восемь тайтлов не
раскладывается никак, но пять файлов из восьми названы штатным загрузчиком и
несут идентификатор перевода.

Имена файлов настоящие, скопированы с сервера.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select

from app.core.db import Base, engine, session_scope
from app.models.catalog import Episode, Series, Translation
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, TitleMapping
from app.services.library_scan import scan_root
from app.services.library_service import _map_episodes, _persist_root

# Тайтл, за который папку принял матчер, и три настоящих фильма в ней.
FOLDER_SERIES = 950001
POPPIN_DREAM = 950002
ROSELIA_I = 950003

FILES = {
    "[Anime365] BanG Dream Movie Poppin Dream - 01 (t4000194).mkv": 4000194,
    "[Anime365] BanG Dream Movie Episode of Roselia - I Yakusoku - 01 (t3670469).mkv": 3670469,
    # Этот никем не подписан — остаётся за папкой.
    "BanG Dream! FILM LIVE.mkv": None,
}


def _seed_catalog() -> None:
    Base.metadata.create_all(engine)
    with session_scope() as session:
        # БД общая на весь прогон: связи от прошлого теста выглядели бы как
        # лишние строки у того же тайтла.
        session.execute(
            delete(EpisodeMapping).where(
                EpisodeMapping.series_id.in_([FOLDER_SERIES, POPPIN_DREAM, ROSELIA_I])
            )
        )
        for series_id, title in (
            (FOLDER_SERIES, "BanG Dream!"),
            (POPPIN_DREAM, "BanG Dream! Movie: Poppin' Dream!"),
            (ROSELIA_I, "BanG Dream! Movie: Episode of Roselia I"),
        ):
            if session.get(Series, series_id) is None:
                session.add(Series(id=series_id, title=title, type="movie"))

        for translation_id, series_id, episode_id in (
            (4000194, POPPIN_DREAM, 950102),
            (3670469, ROSELIA_I, 950103),
        ):
            session.execute(delete(Translation).where(Translation.id == translation_id))
            session.execute(delete(Episode).where(Episode.id == episode_id))
            session.add(Episode(id=episode_id, series_id=series_id, episode_num=1))
            session.add(
                Translation(id=translation_id, series_id=series_id, episode_id=episode_id)
            )


def _links(series_id: int) -> list[tuple[str, float]]:
    with session_scope() as session:
        rows = session.execute(
            select(LibraryFile.filename, EpisodeMapping.episode)
            .join(LibraryFile, EpisodeMapping.file_id == LibraryFile.id)
            .where(EpisodeMapping.series_id == series_id)
        ).all()
    return [(name, episode) for name, episode in rows]


class TestJunkFolder:
    def test_each_downloaded_file_goes_to_its_own_title(self, tmp_path: Path) -> None:
        _seed_catalog()

        root = tmp_path / "movies"
        folder = root / "Bang Dream!"
        folder.mkdir(parents=True)
        for filename in FILES:
            (folder / filename).write_bytes(b"x" * 10)

        root_id = _persist_root(scan_root(str(root), "movie"))
        with session_scope() as session:
            folder_id = session.execute(
                select(LibraryFolder.id).where(LibraryFolder.root_id == root_id)
            ).scalar_one()
            session.execute(
                delete(TitleMapping).where(TitleMapping.folder_id == folder_id)
            )
            session.add(
                TitleMapping(
                    series_id=FOLDER_SERIES,
                    folder_id=folder_id,
                    source="exact_title",
                    confidence=1.0,
                )
            )

        _map_episodes(root_id)

        assert _links(POPPIN_DREAM) == [
            ("[Anime365] BanG Dream Movie Poppin Dream - 01 (t4000194).mkv", 1.0)
        ]
        assert _links(ROSELIA_I) == [
            (
                "[Anime365] BanG Dream Movie Episode of Roselia - I Yakusoku - 01 (t3670469).mkv",
                1.0,
            )
        ]

    def test_unsigned_file_still_belongs_to_the_folder(self, tmp_path: Path) -> None:
        """Файл без `t{id}` разбирать нечем — он остаётся за тайтлом папки."""
        _seed_catalog()

        root = tmp_path / "movies-unsigned"
        folder = root / "Bang Dream!"
        folder.mkdir(parents=True)
        (folder / "BanG Dream! FILM LIVE.mkv").write_bytes(b"x" * 10)

        root_id = _persist_root(scan_root(str(root), "movie"))
        with session_scope() as session:
            folder_id = session.execute(
                select(LibraryFolder.id).where(LibraryFolder.root_id == root_id)
            ).scalar_one()
            session.execute(delete(TitleMapping).where(TitleMapping.folder_id == folder_id))
            session.add(
                TitleMapping(
                    series_id=FOLDER_SERIES,
                    folder_id=folder_id,
                    source="exact_title",
                    confidence=1.0,
                )
            )

        _map_episodes(root_id)

        assert _links(FOLDER_SERIES) == [("BanG Dream! FILM LIVE.mkv", 1.0)]

    def test_unmapped_folder_still_links_signed_files(self, tmp_path: Path) -> None:
        """Папку не опознали — файлы с `t{id}` всё равно доходят до своих тайтлов."""
        _seed_catalog()

        root = tmp_path / "movies-unmapped"
        folder = root / "Свалка"
        folder.mkdir(parents=True)
        name = "[Anime365] BanG Dream Movie Poppin Dream - 01 (t4000194).mkv"
        (folder / name).write_bytes(b"x" * 10)

        root_id = _persist_root(scan_root(str(root), "movie"))
        _map_episodes(root_id)

        assert (name, 1.0) in _links(POPPIN_DREAM)
