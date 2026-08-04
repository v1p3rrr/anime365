"""Подключение к SQLite.

Режим WAL обязателен: фоновый синк каталога пишет в БД одновременно с тем, как UI читает,
и без WAL читатели блокировали бы писателя.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects.sqlite.base import SQLiteCompiler
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

log = logging.getLogger(__name__)


def _sqlite_from_hint(_self, _table, hint: str) -> str:
    """Отдаёт подсказку об индексе как есть: `FROM series INDEXED BY ix_…`.

    Зачем вообще подсказка. Запросы каталога отличаются друг от друга только
    сочетанием фильтра и порядка сортировки, и у всех них есть покрывающий
    индекс. Но выбирает индекс планировщик, а он при равной оценке стоимости
    может взять тот, что покрывает фильтр и не покрывает сортировку, — и тогда
    недостающую колонку приходится дочитывать из широкой таблицы. Это не
    теория: локальная SQLite 3.49 и контейнерная 3.46 на одних и тех же
    запросах выбирают **разные** индексы, то есть обновление системного пакета
    однажды тихо вернуло бы секундные ответы ([D-84](../../../claude/09-decisions.md)).

    Почему патч. `with_hint()` у SQLAlchemy есть, но для SQLite нет реализации
    отрисовки: базовый `get_from_hint_text` возвращает None, и подсказка либо
    пропадает, либо (в зависимости от пути) печатается как `[INDEXED BY …]`,
    что SQLite поймёт как псевдоним таблицы в квадратных скобках. Одна строка
    здесь дешевле, чем свой диалект или сборка этих запросов текстом.
    """
    return hint


SQLiteCompiler.get_from_hint_text = _sqlite_from_hint  # type: ignore[method-assign]

# Единственная настройка, которая не может жить в реестре: сам реестр читает
# переопределения из БД, поэтому путь к БД обязан быть известен раньше.
DB_PATH = Path(os.environ.get("A365_DB_PATH", "/data/anime365.db"))


class Base(DeclarativeBase):
    pass


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cur = dbapi_connection.cursor()
    # WAL — читатели не блокируют писателя.
    cur.execute("PRAGMA journal_mode=WAL")
    # NORMAL достаточно при WAL и заметно быстрее FULL на слабом диске.
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    # Ждём вместо мгновенного "database is locked", пока пишет фоновая джоба.
    cur.execute("PRAGMA busy_timeout=10000")
    # Временные структуры (сортировки, FTS-слияния) в памяти, а не на диске.
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.close()


def create_db_engine(db_path: Path = DB_PATH) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        # Соединение переиспользуется воркерами из разных потоков.
        connect_args={"check_same_thread": False},
        # SQLite не выигрывает от большого пула, а память на слабом сервере экономим.
        pool_size=5,
        max_overflow=5,
        future=True,
    )
    event.listen(engine, "connect", _configure_sqlite)
    return engine


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Транзакция с автоматическим commit/rollback."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """Зависимость FastAPI."""
    with session_scope() as session:
        yield session


def check_connection() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        log.exception("Не удалось подключиться к БД")
        return False
