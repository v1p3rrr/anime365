"""Выбор основной папки переживает исчезновение привязки под руками.

⚠️ Заведено по 500-й на живом сервере:

    GET /api/series/40024 → 500
    StaleDataError: UPDATE statement on table 'title_mapping'
    expected to update 1 row(s); 0 were matched.

Причина — гонка, а не логика. Страница тайтла выбирает основную папку, а
сканирование библиотеки в этот же момент имеет полное право снять привязку и
завести новую: на сервере сканы шли непрерывно, один за другим. ORM в такой
ситуации проверяет, что её UPDATE задел ровно одну строку, не находит её и
падает — вместе со всей страницей.

Правильное поведение здесь — равнодушие: привязки нет, назначать её основной
незачем, страница обязана открыться. Поэтому пишем `UPDATE … WHERE id = …`
напрямую, а не через изменение объектов сессии.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.library import LibraryFolder, LibraryRoot, TitleMapping
from app.services import mapping_info
from app.services.mapping_info import ensure_primary_mapping

SERIES_ID = 960_101
ROOT_PATH = "/tmp/a365-primary-race"


@pytest.fixture(autouse=True)
def _clean():
    _wipe()
    yield
    _wipe()


def _wipe() -> None:
    with session_scope() as session:
        session.execute(delete(LibraryRoot).where(LibraryRoot.path == ROOT_PATH))


def _two_folders_on_one_title() -> list[int]:
    """Раскладка, где выбор основной вообще имеет смысл: папок две.

    Так на сервере и было: `GATE` и `Gate: Jieitai Kanochi nite, Kaku Tatakaeri`,
    обе привязаны к одному тайтлу.
    """
    with session_scope() as session:
        if session.get(Series, SERIES_ID) is None:
            session.add(Series(id=SERIES_ID, title="GATE", type="tv"))

        root = LibraryRoot(path=ROOT_PATH, kind="series")
        session.add(root)
        session.flush()

        mapping_ids = []
        for name, videos in (("GATE", 24), ("Gate: Jieitai", 12)):
            folder = LibraryFolder(
                root_id=root.id, rel_path=name, name=name, video_count=videos
            )
            session.add(folder)
            session.flush()
            mapping = TitleMapping(
                series_id=SERIES_ID, folder_id=folder.id, source="fuzzy", confidence=90.0
            )
            session.add(mapping)
            session.flush()
            mapping_ids.append(mapping.id)
        return mapping_ids


class _CountsThatVanish(dict):
    """Вклад по папкам, который сносит привязку при первом же обращении.

    Момент выбран не случайно: `rank()` вызывается внутри уже открытой
    транзакции, после чтения привязок и до их записи. Ровно в этот зазор и
    попадало сканирование.
    """

    def __init__(self, victim_id: int) -> None:
        super().__init__()
        self._victim = victim_id
        self.fired = False

    def get(self, key, default=None):  # type: ignore[no-untyped-def]
        if not self.fired:
            self.fired = True
            with session_scope() as session:
                session.execute(delete(TitleMapping).where(TitleMapping.id == self._victim))
        return super().get(key, default)


class TestNormalCase:
    def test_the_folder_with_more_episodes_wins(self) -> None:
        ensure_primary_mapping(SERIES_ID)
        _two_folders_on_one_title()

        chosen = ensure_primary_mapping(SERIES_ID)

        with session_scope() as session:
            primary = session.execute(
                select(TitleMapping.id).where(
                    TitleMapping.series_id == SERIES_ID, TitleMapping.is_primary.is_(True)
                )
            ).scalars().all()

        assert primary == [chosen], "основная должна быть ровно одна"

    def test_a_title_without_folders_is_not_an_error(self) -> None:
        assert ensure_primary_mapping(SERIES_ID) is None


class TestTheRace:
    def test_a_binding_deleted_mid_flight_does_not_break_the_page(self, monkeypatch) -> None:
        """Тот самый случай: строка исчезла между чтением и записью."""
        mapping_ids = _two_folders_on_one_title()
        counts = _CountsThatVanish(mapping_ids[0])
        monkeypatch.setattr(mapping_info, "get_per_folder_counts", lambda _sid: counts)

        # Без правки здесь падало StaleDataError, а с ним — вся страница тайтла.
        ensure_primary_mapping(SERIES_ID)

        assert counts.fired, "гонка не воспроизвелась — тест ничего не проверил"

    def test_the_surviving_binding_is_still_usable(self, monkeypatch) -> None:
        """Пережить мало: оставшаяся привязка должна остаться осмысленной."""
        mapping_ids = _two_folders_on_one_title()
        counts = _CountsThatVanish(mapping_ids[0])
        monkeypatch.setattr(mapping_info, "get_per_folder_counts", lambda _sid: counts)

        ensure_primary_mapping(SERIES_ID)

        with session_scope() as session:
            left = session.execute(
                select(TitleMapping.id).where(TitleMapping.series_id == SERIES_ID)
            ).scalars().all()
            primaries = session.execute(
                select(TitleMapping.id).where(
                    TitleMapping.series_id == SERIES_ID, TitleMapping.is_primary.is_(True)
                )
            ).scalars().all()

        assert left == [mapping_ids[1]], "снесённая привязка не должна воскреснуть"
        assert len(primaries) <= 1, "двух основных не бывает"
