"""Лёгкие миграции схемы для SQLite.

Полноценный Alembic здесь избыточен: схема простая, а каталог — это кэш, который
восстанавливается из API за минуту. Но ронять сервис при добавлении колонки нельзя,
поэтому недостающие колонки добавляются автоматически при старте.

Если понадобится менять типы или удалять колонки — заводим Alembic, SQLite такое
через ALTER не умеет.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.core.db import Base, engine

log = logging.getLogger(__name__)

# Тип в SQLite нестрогий, поэтому достаточно указать affinity.
_TYPE_MAP = {
    "INTEGER": "INTEGER",
    "VARCHAR": "TEXT",
    "TEXT": "TEXT",
    "FLOAT": "REAL",
    "BOOLEAN": "INTEGER",
    "JSON": "TEXT",
    "DATETIME": "TEXT",
}


def _sqlite_type(column) -> str:
    compiled = str(column.type).upper()
    for key, value in _TYPE_MAP.items():
        if compiled.startswith(key):
            return value
    return "TEXT"


def add_missing_columns() -> int:
    """Добавляет колонки, появившиеся в моделях после создания таблиц."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added = 0

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue

            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue

                # NOT NULL без значения по умолчанию SQLite добавить не даст,
                # поэтому новые колонки должны быть nullable.
                if not column.nullable and column.default is None:
                    log.warning(
                        "Пропускаю колонку %s.%s: она NOT NULL без значения по умолчанию, "
                        "нужна ручная миграция",
                        table.name,
                        column.name,
                    )
                    continue

                conn.execute(
                    text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {_sqlite_type(column)}')
                )
                log.info("Добавлена колонка %s.%s", table.name, column.name)
                added += 1

    return added


def add_missing_indexes() -> int:
    """Создаёт индексы, появившиеся в моделях после создания таблиц.

    ⚠️ Без этого новый индекс получали бы только чистые установки: `create_all`
    делает индексы вместе с таблицей, а на существующей базе не делает ничего.
    Ровно та же ловушка, что была с колонками, — и цена у неё тут выше: не
    «поле пустое», а «каталог открывается секунду вместо миллисекунды», и
    заметить это без замера нельзя.

    Индексы создаются по одному: `CREATE INDEX` на 29 тысячах строк — это доли
    секунды, но каждый берёт writer-lock, и делать это одной транзакцией на все
    сразу незачем.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    created = 0

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue

        present = {idx["name"] for idx in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name in present:
                continue
            with engine.begin() as conn:
                index.create(bind=conn, checkfirst=True)
            log.info("Создан индекс %s", index.name)
            created += 1

    return created
