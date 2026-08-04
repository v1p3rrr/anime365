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


#: Индексы, которые были в прежних версиях и больше не нужны.
#:
#: Сами по себе они не ломают ничего, но и не бесплатны: каждый удорожает
#: запись, а главное — участвует в выборе плана. Оба перечисленных покрывали
#: фильтр, но не сортировку, и планировщик, выбрав такой, дочитывал недостающую
#: колонку из широкой таблицы ([D-84](../../../claude/09-decisions.md)).
#:
#: Список явный, а не «всё, чего нет в моделях»: под таким правилом снесло бы и
#: служебные индексы FTS5, и автоиндексы SQLite.
OBSOLETE_INDEXES = {
    "series": ("ix_series_catalog_airing", "ix_series_catalog_type"),
}


def drop_obsolete_indexes() -> int:
    """Убирает индексы, которые остались от прежних версий."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    dropped = 0

    for table, names in OBSOLETE_INDEXES.items():
        if table not in existing_tables:
            continue
        present = {idx["name"] for idx in inspector.get_indexes(table)}
        for name in names:
            if name not in present:
                continue
            with engine.begin() as conn:
                conn.execute(text(f'DROP INDEX "{name}"'))
            log.info("Удалён устаревший индекс %s", name)
            dropped += 1

    return dropped


def sync_indexes() -> int:
    """Приводит индексы базы к тому, что объявлено в моделях.

    ⚠️ Без этого новый индекс получали бы только чистые установки: `create_all`
    делает индексы вместе с таблицей, а на существующей базе не делает ничего.
    Ровно та же ловушка, что была с колонками, — и цена у неё тут выше: не
    «поле пустое», а «каталог открывается секунду вместо миллисекунды», и
    заметить это без замера нельзя.

    ⚠️ Второй случай той же ловушки, на который наступили позже: индекс с уже
    занятым именем, но другим набором колонок. Проверка «есть индекс с таким
    именем» пропустила бы его, и база осталась бы со старым определением —
    навсегда и молча. Поэтому сверяется список колонок, а расхождение чинится
    пересозданием.

    Индексы создаются по одному: `CREATE INDEX` на 29 тысячах строк — это доли
    секунды, но каждый берёт writer-lock, и делать это одной транзакцией на все
    сразу незачем.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    changed = 0

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue

        present = {idx["name"]: idx for idx in inspector.get_indexes(table.name)}
        for index in table.indexes:
            wanted = [c.name for c in index.columns]
            current = present.get(index.name)

            if current is not None:
                if list(current.get("column_names") or []) == wanted:
                    continue
                with engine.begin() as conn:
                    conn.execute(text(f'DROP INDEX "{index.name}"'))
                log.info(
                    "Индекс %s пересоздаётся: было %s, стало %s",
                    index.name,
                    current.get("column_names"),
                    wanted,
                )

            with engine.begin() as conn:
                index.create(bind=conn, checkfirst=True)
            if current is None:
                log.info("Создан индекс %s", index.name)
            changed += 1

    return changed
