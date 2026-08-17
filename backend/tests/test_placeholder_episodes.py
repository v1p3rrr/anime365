"""Заготовки серий, заведённые сайтом впрок, на страницу тайтла не попадают.

Живой случай, с которого всё началось: у «Врат» (тайтл 12074) под первым куром
лежат 24 серии при `numberOfEpisodes: 12`. Вторые двенадцать — пустые записи:
второй кур давно живёт отдельным тайтлом (13823), переводов у заготовок нет и
не будет. Страница показывала два кура одним тайтлом и обещала серии, которых
на сайте нет.

Признак заготовки — `isActive`, и он проверен на живых данных: у 45 случайных
тайтлов из 710 эпизодов 126 неактивных, и ни у одного из них нет ни одного
перевода. Обратное правило («нет переводов — значит заготовка») не годится:
182 активных эпизода без переводов — обычные серии, к которым просто ничего не
выложили.
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile

import pytest
from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Episode, Series, Translation
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, LibraryRoot
from app.services.title_service import build_title_page

SERIES = 980_001
ROOT = str(pathlib.Path(tempfile.gettempdir()) / "a365-placeholder-tests")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    # Страница не ходит в сеть: переводы у неё из кэша, обновление — фоном.
    monkeypatch.setattr(
        "app.services.title_service.schedule_translation_refresh", lambda series_id: False
    )

    def wipe() -> None:
        with session_scope() as session:
            session.execute(delete(EpisodeMapping).where(EpisodeMapping.series_id == SERIES))
            session.execute(delete(Translation).where(Translation.series_id == SERIES))
            session.execute(delete(Episode).where(Episode.series_id == SERIES))
            session.execute(delete(Series).where(Series.id == SERIES))
            root = session.execute(
                select(LibraryRoot).where(LibraryRoot.path == ROOT)
            ).scalar_one_or_none()
            if root is not None:
                session.execute(delete(LibraryFolder).where(LibraryFolder.root_id == root.id))

    wipe()
    yield
    wipe()


def _title(series_type: str = "tv", planned: int = 12) -> None:
    with session_scope() as session:
        session.merge(
            Series(
                id=SERIES,
                title="Two Cours",
                type=series_type,
                number_of_episodes=planned,
                # Иначе страница пойдёт в Anime365 за подробностями.
                has_details=True,
            )
        )


def _episode(number: float, *, active: bool = True, episode_type: str = "tv") -> int:
    episode_id = SERIES * 10 + int(number)
    with session_scope() as session:
        session.merge(
            Episode(
                id=episode_id,
                series_id=SERIES,
                episode_int=str(int(number)),
                episode_num=number,
                episode_type=episode_type,
                is_active=active,
            )
        )
    return episode_id


def _translation(tid: int, episode_id: int, lang: str = "ru", kind: str = "sub") -> None:
    with session_scope() as session:
        session.merge(
            Translation(
                id=tid,
                series_id=SERIES,
                episode_id=episode_id,
                type_kind=kind,
                type_lang=lang,
                authors_summary="Студия",
                is_active=True,
            )
        )


def _local_video(number: float) -> None:
    """Файл серии на диске — вместе с папкой и корнем, которых требует связь."""
    with session_scope() as session:
        root = session.execute(
            select(LibraryRoot).where(LibraryRoot.path == ROOT)
        ).scalar_one_or_none()
        if root is None:
            root = LibraryRoot(path=ROOT, kind="series")
            session.add(root)
            session.flush()
        folder = session.execute(
            select(LibraryFolder).where(
                LibraryFolder.root_id == root.id, LibraryFolder.rel_path == "Two Cours"
            )
        ).scalar_one_or_none()
        if folder is None:
            folder = LibraryFolder(root_id=root.id, rel_path="Two Cours", name="Two Cours")
            session.add(folder)
            session.flush()
        file = LibraryFile(
            folder_id=folder.id,
            filename=f"Two Cours S01E{int(number):02d}.mkv",
            kind="video",
            size_bytes=1,
            mtime=1.0,
            episode=number,
        )
        session.add(file)
        session.flush()
        session.add(
            EpisodeMapping(
                series_id=SERIES, episode=number, file_id=file.id, role="video"
            )
        )


def _page() -> dict:
    return asyncio.run(build_title_page(SERIES))


class TestSecondCourIsNotShown:
    def test_placeholders_are_absent_from_the_episode_list(self) -> None:
        _title()
        for number in range(1, 13):
            _episode(number)
        for number in range(13, 25):
            _episode(number, active=False)

        page = _page()

        assert [e["episode_num"] for e in page["episodes"]] == [float(n) for n in range(1, 13)]

    def test_placeholders_do_not_inflate_the_total(self) -> None:
        """Из-за них знаменатель показывал 24 серии вместо двенадцати."""
        _title()
        for number in range(1, 13):
            _episode(number)
        for number in range(13, 25):
            _episode(number, active=False)

        assert _page()["total_episodes"] == 12

    def test_a_placeholder_with_a_local_file_stays_visible(self) -> None:
        """Свои файлы важнее чужого флага: спрятать серию, которая лежит на
        диске, значит спрятать данные пользователя."""
        _title()
        _episode(1)
        _episode(13, active=False)
        _local_video(13.0)

        assert [e["episode_num"] for e in _page()["episodes"]] == [1.0, 13.0]


class TestOvaTitleCountsItsOwnEpisodes:
    """Тайтл 15594 показывал «4 авторов · 0 переводов» при восемнадцати живых."""

    def test_translations_of_an_ova_title_are_counted(self) -> None:
        _title(series_type="ova", planned=1)
        episode_id = _episode(1, episode_type="ova")
        _translation(1, episode_id)
        _translation(2, episode_id)

        section = next(s for s in _page()["summary"] if s["kind"] == "sub")

        assert section["total_translations"] == 2
        assert section["groups"][0]["count"] == 2

    def test_an_opening_does_not_become_the_only_episode(self) -> None:
        _title(series_type="ova", planned=1)
        ova = _episode(1, episode_type="ova")
        opening = _episode(2, episode_type="opening")
        _translation(1, ova)
        _translation(2, opening)

        page = _page()
        section = next(s for s in page["summary"] if s["kind"] == "sub")

        assert section["total_translations"] == 1
        assert [e["episode_type"] for e in page["episodes"]] == ["ova", "opening"]
