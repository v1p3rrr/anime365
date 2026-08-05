"""Привязка отдельной серии к папке и файлу, лежащим в стороне.

Живой случай: у Girls Band Cry в каталоге одно вхождение с двумя сериями, а
вторая часть лежит **отдельной папкой в корне фильмов**, к тайтлу не
привязанной. Окрестности тайтла для неё не хватает: в папках первой части её
нет, а у соседнего вхождения папок нет вовсе — раньше окно назначения молча
оказывалось пустым.

Привязка серии к файлу папку тайтла не требует, поэтому и выбор ограничивать
ею незачем.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot, TitleMapping

BOUND = 970001  # тайтл с привязанной папкой первой части
LOOSE = 970002  # тайтл вообще без папок

PART_ONE = "Girls Band Cry Movie 1 - Youth Rhapsody 青春狂走曲"
PART_TWO = "Girls Band Cry Movie 2 - Hey, Our Future  なぁ、未来。"
PART_TWO_FILE = "GIRLS BAND CRY The Movie - Hey, Our Future (2025) - [Bluray-1080p].mkv"


def _seed() -> None:
    with session_scope() as session:
        for series_id, title in ((BOUND, "Girls Band Cry Movie"), (LOOSE, "Girls Band Cry Movie 2")):
            if session.get(Series, series_id) is None:
                session.add(Series(id=series_id, title=title, type="movie"))

        root = session.execute(
            select(LibraryRoot).where(LibraryRoot.path == "/test/movies-binding")
        ).scalar_one_or_none()
        if root is None:
            root = LibraryRoot(path="/test/movies-binding", kind="movie")
            session.add(root)
            session.flush()

        session.execute(delete(LibraryFolder).where(LibraryFolder.root_id == root.id))
        session.flush()

        first = LibraryFolder(root_id=root.id, rel_path=PART_ONE, name=PART_ONE, video_count=1)
        second = LibraryFolder(root_id=root.id, rel_path=PART_TWO, name=PART_TWO, video_count=1)
        session.add_all([first, second])
        session.flush()

        session.add(
            LibraryFile(
                folder_id=second.id,
                filename=PART_TWO_FILE,
                kind="video",
                size_bytes=10,
                mtime=1.0,
            )
        )
        session.execute(delete(TitleMapping).where(TitleMapping.series_id.in_([BOUND, LOOSE])))
        session.add(
            TitleMapping(series_id=BOUND, folder_id=first.id, source="fuzzy", confidence=0.9)
        )


def _get(client, url: str) -> Any:
    response = client.get(url)
    assert response.status_code == 200, response.text
    return response.json()


class TestTargetFolders:
    def test_neighbourhood_does_not_include_a_folder_standing_apart(self, client) -> None:
        _seed()
        paths = [f["rel_path"] for f in _get(client, f"/api/library/episode-targets/{BOUND}/folders")]

        assert PART_ONE in paths
        assert PART_TWO not in paths, "иначе тест не про то — папка должна быть вне окрестности"

    def test_search_finds_it_anywhere(self, client) -> None:
        _seed()
        paths = [
            f["rel_path"]
            for f in _get(
                client, f"/api/library/episode-targets/{BOUND}/folders?query=hey, our future"
            )
        ]

        assert PART_TWO in paths

    def test_title_without_folders_can_still_search(self, client) -> None:
        """Раньше здесь всегда был пустой список — и назначить было нечего."""
        _seed()

        assert _get(client, f"/api/library/episode-targets/{LOOSE}/folders") == []

        paths = [
            f["rel_path"]
            for f in _get(client, f"/api/library/episode-targets/{LOOSE}/folders?query=girls band")
        ]
        assert PART_TWO in paths


class TestSeriesFiles:
    def test_without_query_only_the_titles_own_files(self, client) -> None:
        _seed()
        names = [f["filename"] for f in _get(client, f"/api/library/series/{BOUND}/files")]

        assert PART_TWO_FILE not in names

    def test_query_searches_the_whole_library(self, client) -> None:
        _seed()
        names = [
            f["filename"]
            for f in _get(client, f"/api/library/series/{BOUND}/files?query=hey, our future")
        ]

        assert PART_TWO_FILE in names

    def test_folder_name_matches_too(self, client) -> None:
        """Искать удобнее по папке: имя файла её названия может не повторять."""
        _seed()
        names = [
            f["filename"]
            for f in _get(client, f"/api/library/series/{BOUND}/files?query=Movie 2")
        ]

        assert PART_TWO_FILE in names

    def test_missing_query_gives_nothing_extra(self, client) -> None:
        _seed()
        assert _get(client, f"/api/library/series/{LOOSE}/files?query=заведомо-нет-такого") == []
