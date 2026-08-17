"""Досбор переводов, на которые ссылаются имена файлов.

Связь по `t{id}` точная и сильнее папки, но искалась она в **локальном кэше**
переводов, а кэш наполняется лениво — при открытии страницы тайтла. Не
открывали страницу — перевода в базе нет — файл со своим точным
идентификатором не опознавался ничем, кроме папки вокруг него. Это было
записано пределом алгоритма в `16-mapping-pipeline.md`.

⚠️ Главная проверка здесь — последняя: досбор идёт **после** связки серий, то
есть та уже отработала со старым кэшем. Без пересвязки результат досбора
появился бы только при следующем сканировании, и первый скан выглядел бы
бесполезным.

Сети в тестах нет: клиент и загрузка тайтла замоканы.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.core.db import Base, engine, session_scope
from app.models.catalog import Episode, Series, Translation
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, TitleMapping
from app.services.library_scan import scan_root
from app.services.library_service import _backfill_own_downloads, _persist_root
from app.services.translation_backfill import unknown_translation_ids

SERIES_ID = 940_001
KNOWN_TRANSLATION = 4_940_001
UNKNOWN_TRANSLATION = 4_940_002
EPISODE_ID = 940_101

#: Настоящая схема именования: файл, названный сайтом, несёт `t{id}`.
FILENAME = f"[Anime365] Backfill Test - 01 (t{UNKNOWN_TRANSLATION}).mkv"


#: Имена папок, которые заводят тесты модуля.
FOLDER_NAMES = ("Backfill Test", "No Ids")


@pytest.fixture(autouse=True)
def clean():
    """Убирает за собой **по содержимому**, а не по запомненным идентификаторам.

    ⚠️ Файл тестовой БД лежит во временной папке ОС и переживает прогоны
    (см. `conftest._schema`). Папки, оставшиеся от прошлого запуска, никуда не
    деваются, и пересвязка честно находит в каждой файл с тем же `t{id}` — было
    четыре привязки вместо одной. Список идентификаторов, собранный в текущем
    процессе, про такие папки не знает; имена — знают.
    """
    Base.metadata.create_all(engine)

    def wipe() -> None:
        with session_scope() as session:
            session.execute(
                delete(EpisodeMapping).where(EpisodeMapping.series_id == SERIES_ID)
            )
            session.execute(delete(Translation).where(Translation.series_id == SERIES_ID))
            session.execute(delete(Episode).where(Episode.series_id == SERIES_ID))
            session.execute(delete(Series).where(Series.id == SERIES_ID))

            folders = session.execute(
                select(LibraryFolder.id).where(LibraryFolder.name.in_(FOLDER_NAMES))
            ).scalars().all()
            if folders:
                session.execute(delete(LibraryFile).where(LibraryFile.folder_id.in_(folders)))
                session.execute(delete(LibraryFolder).where(LibraryFolder.id.in_(folders)))

    wipe()
    with session_scope() as session:
        session.add(Series(id=SERIES_ID, title="Backfill Test", type="tv"))
    yield
    wipe()


def _cache_translation(translation_id: int) -> None:
    """Кладёт перевод в кэш — так же, как это сделала бы загрузка тайтла."""
    with session_scope() as session:
        if session.get(Episode, EPISODE_ID) is None:
            session.add(Episode(id=EPISODE_ID, series_id=SERIES_ID, episode_num=1))
        session.merge(
            Translation(
                id=translation_id,
                series_id=SERIES_ID,
                episode_id=EPISODE_ID,
                type_kind="sub",
                type_lang="ru",
            )
        )


def _library(tmp_path: Path) -> int:
    root = tmp_path / "series"
    folder = root / "Backfill Test"
    folder.mkdir(parents=True)
    (folder / FILENAME).write_bytes(b"x" * 10)
    root_id = _persist_root(scan_root(str(root), "series"))
    with session_scope() as session:
        folder_id = session.execute(
            select(LibraryFolder.id).where(LibraryFolder.root_id == root_id)
        ).scalar_one()
        session.execute(delete(TitleMapping).where(TitleMapping.folder_id == folder_id))
    return root_id


def _links() -> list[float]:
    with session_scope() as session:
        return sorted(
            session.execute(
                select(EpisodeMapping.episode).where(EpisodeMapping.series_id == SERIES_ID)
            ).scalars()
        )


class TestWhatCountsAsUnknown:
    def test_a_cached_translation_is_not_requested(self, tmp_path: Path) -> None:
        _library(tmp_path)
        _cache_translation(UNKNOWN_TRANSLATION)

        assert UNKNOWN_TRANSLATION not in unknown_translation_ids()

    def test_a_missing_translation_is(self, tmp_path: Path) -> None:
        _library(tmp_path)

        assert UNKNOWN_TRANSLATION in unknown_translation_ids()

    def test_files_without_an_id_are_not_asked_about(self, tmp_path: Path) -> None:
        root = tmp_path / "series"
        folder = root / "No Ids"
        folder.mkdir(parents=True)
        (folder / "Show S01E01 [Doki] Bluray-1080p.mkv").write_bytes(b"x" * 10)
        _persist_root(scan_root(str(root), "series"))

        with session_scope() as session:
            stored = session.execute(
                select(LibraryFile.anime365_translation_id).where(
                    LibraryFile.filename.like("Show S01E01%")
                )
            ).scalars().all()

        assert stored and all(value is None for value in stored)


class TestTheSameScanLinksTheFile:
    """Ради этого всё и затевалось: файл привязан по итогам ЭТОГО прохода."""

    def test_file_is_linked_after_the_backfill(self, tmp_path: Path, monkeypatch) -> None:
        _library(tmp_path)
        assert _links() == [], "до досбора привязывать нечем"

        async def fake_backfill() -> dict:
            # Настоящий досбор сходил бы в сеть за seriesId и догрузил тайтл;
            # здесь тот же результат кладётся в базу напрямую.
            _cache_translation(UNKNOWN_TRANSLATION)
            return {"unknown": 1, "series": {SERIES_ID}, "loaded": 1}

        monkeypatch.setattr(
            "app.services.translation_backfill.backfill", fake_backfill
        )

        result = _backfill_own_downloads()

        assert result["relinked"] >= 1
        assert _links() == [1.0], "файл не привязался в том же проходе"

    def test_a_failing_backfill_does_not_break_the_scan(self, monkeypatch) -> None:
        """Сканирование к этому моменту уже сделано и полезно само по себе."""

        def explode() -> dict:
            raise RuntimeError("сайт не ответил")

        monkeypatch.setattr(
            "app.services.translation_backfill.backfill_blocking", explode
        )

        assert "error" in _backfill_own_downloads()

    def test_nothing_to_do_is_not_an_error(self, monkeypatch) -> None:
        async def empty() -> dict:
            return {"unknown": 0, "series": set(), "loaded": 0}

        monkeypatch.setattr("app.services.translation_backfill.backfill", empty)

        assert _backfill_own_downloads() == {"unknown": 0, "loaded": 0, "relinked": 0}
