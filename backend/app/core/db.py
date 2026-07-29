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
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

log = logging.getLogger(__name__)

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
