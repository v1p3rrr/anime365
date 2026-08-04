"""Долгие пишущие транзакции — источник «database is locked».

Файл заведён после разбора логов с сервера: там падали и запись прогресса
загрузки, и сохранение курсора синка. Причина оказалась не в SQLite, а в нас —
две операции держали writer-lock десятками секунд, а SQLite допускает одного
писателя. Замеры в контейнере на настоящем каталоге (29 214 тайтлов):

* пересборка FTS — **36,5 с одной транзакцией**, и за это время три коротких
  записи из четырнадцати упали с «database is locked», а остальные ждали до
  предела `busy_timeout` в 10 секунд;
* сопоставление папок — 0,83 с на семи папках эталонной библиотеки, то есть
  около 0,12 с на папку: на тысяче папок это две минуты блокировки.

Тесты ниже сторожат не скорость, а **границы транзакций**: чтобы никто не
завернул это обратно в одну `session_scope()`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, select

from app.core.db import engine, session_scope
from app.models.catalog import Series
from app.models.library import LibraryFolder, LibraryRoot, TitleMapping
from app.services import library_service
from app.services.fts import FTS_CHUNK, rebuild_fts_for
from app.services.library_service import MAPPING_WRITE_BATCH, _map_folders


class CommitCounter:
    """Считает коммиты и умеет сказать, открыта ли сейчас пишущая транзакция."""

    def __init__(self) -> None:
        self.commits = 0
        self.writing = False

    def __enter__(self) -> "CommitCounter":
        event.listen(engine, "before_cursor_execute", self._before)
        event.listen(engine, "commit", self._commit)
        return self

    def __exit__(self, *_exc) -> None:
        event.remove(engine, "before_cursor_execute", self._before)
        event.remove(engine, "commit", self._commit)

    def _before(self, conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip()[:6].upper().startswith(("INSERT", "UPDATE", "DELETE")):
            self.writing = True

    def _commit(self, conn) -> None:
        if self.writing:
            self.commits += 1
            self.writing = False


@pytest.fixture
def catalog():
    """Тайтлов заметно больше, чем размер порции пересборки индекса."""
    ids = list(range(900_001, 900_001 + FTS_CHUNK * 2 + 10))
    with session_scope() as session:
        for series_id in ids:
            if session.get(Series, series_id) is None:
                session.add(
                    Series(
                        id=series_id,
                        title=f"Проверка {series_id}",
                        titles={"romaji": f"Test {series_id}"},
                        type="tv",
                    )
                )
    yield ids
    with session_scope() as session:
        session.query(Series).filter(Series.id.in_(ids)).delete(synchronize_session=False)


class TestFtsRebuild:
    def test_uses_many_short_transactions(self, catalog) -> None:
        """Одна транзакция на весь каталог держала лок 36 секунд."""
        with CommitCounter() as counter:
            rebuild_fts_for(catalog)

        assert counter.commits >= len(catalog) // FTS_CHUNK, (
            f"пересборка уложилась в {counter.commits} транзакций — похоже, "
            "она снова обёрнута в одну на весь список"
        )

    def test_index_is_complete(self, catalog) -> None:
        """Порции не должны терять записи: индекс обязан покрыть весь список."""
        from sqlalchemy import text

        rebuild_fts_for(catalog)
        with session_scope() as session:
            placeholders = ", ".join(str(int(i)) for i in catalog)
            found = session.execute(
                text(f"SELECT COUNT(*) FROM series_fts WHERE series_id IN ({placeholders})")
            ).scalar()
        assert found == len(catalog)


@pytest.fixture
def many_folders(tmp_path):
    """Корень с числом папок больше размера порции записи."""
    count = MAPPING_WRITE_BATCH * 2 + 5
    with session_scope() as session:
        session.query(TitleMapping).delete()
        session.query(LibraryFolder).delete()
        session.query(LibraryRoot).delete()

    with session_scope() as session:
        root = LibraryRoot(kind="series", path=str(tmp_path), is_available=True)
        session.add(root)
        session.flush()
        for n in range(count):
            session.add(
                LibraryFolder(
                    root_id=root.id,
                    rel_path=f"Show {n}",
                    name=f"Show {n}",
                    video_count=1,
                )
            )
        session.flush()
        root_id = root.id

    yield root_id, count

    with session_scope() as session:
        session.query(TitleMapping).delete()
        session.query(LibraryFolder).delete()
        session.query(LibraryRoot).delete()


class TestFolderMapping:
    def test_matching_runs_outside_a_write_transaction(self, many_folders, monkeypatch) -> None:
        """Главное: нечёткий поиск не должен идти под открытой записью.

        Именно из-за этого блокировка держалась минутами на большой библиотеке.
        """
        root_id, _ = many_folders
        observed: list[bool] = []

        class Probe:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def match(self, _ctx):
                observed.append(counter.writing)
                return None

        monkeypatch.setattr(library_service, "Matcher", Probe)

        with CommitCounter() as counter:
            _map_folders(root_id, index=object(), scope="series")

        assert observed, "сопоставление не вызывалось — тест ничего не проверил"
        assert not any(observed), (
            "во время сопоставления была открыта пишущая транзакция — "
            "значит матчинг снова внутри session_scope()"
        )

    def test_all_folders_are_processed(self, many_folders, monkeypatch) -> None:
        """Разбиение на порции не должно терять папки."""
        root_id, count = many_folders
        seen: list[str] = []

        class Probe:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def match(self, ctx):
                seen.append(ctx.folder_name)
                return None

        monkeypatch.setattr(library_service, "Matcher", Probe)
        stats = _map_folders(root_id, index=object(), scope="series")

        assert len(seen) == count
        assert stats["unmatched"] == count

    def test_manual_mappings_are_never_touched(self, many_folders, monkeypatch) -> None:
        """Ручная привязка — решение пользователя, порции этого не меняют."""
        root_id, count = many_folders
        with session_scope() as session:
            if session.get(Series, 900_999) is None:
                session.add(Series(id=900_999, title="Ручной", type="tv"))
            folder_id = session.execute(
                select(LibraryFolder.id).where(LibraryFolder.root_id == root_id).limit(1)
            ).scalar_one()
            session.add(
                TitleMapping(
                    series_id=900_999,
                    folder_id=folder_id,
                    source="manual",
                    confidence=100.0,
                    is_manual=True,
                )
            )

        class Probe:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def match(self, _ctx):
                return None

        monkeypatch.setattr(library_service, "Matcher", Probe)
        stats = _map_folders(root_id, index=object(), scope="series")

        assert stats["manual_kept"] == 1
        with session_scope() as session:
            kept = session.execute(
                select(TitleMapping).where(TitleMapping.folder_id == folder_id)
            ).scalars().all()
        assert len(kept) == 1 and kept[0].is_manual
