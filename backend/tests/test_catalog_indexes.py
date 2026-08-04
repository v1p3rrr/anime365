"""Запросы каталога обязаны отвечаться по индексу, а не сканом таблицы.

Почему это отдельный тест, а не «и так видно». Таблица `series` широкая: в
каждой строке лежат `titles`, `all_titles`, `descriptions` и `links` — вместе
около 25 МБ на 29 тысяч строк. Полный скан читает всю эту массу, даже когда из
строки нужен один `id`. Замерено в контейнере на настоящем каталоге:

| запрос | без индексов | с индексами |
|---|---|---|
| `COUNT` выборки | 1229 мс | 0,6 мс |
| каталог по умолчанию (сортировка по оценке) | 1098 мс | 0,2 мс |
| сортировка по названию | 1179 мс | 0,2 мс |
| онгоинги | 0,4 мс | 0,2 мс |
| фильтр по типу | 744 мс | 0,2 мс |

То есть открытие каталога стоило больше двух секунд работы диска — `COUNT`
плюс окно, — и никакой кэш на клиенте этого бы не исправил: при первом заходе
данных у него всё равно нет.

⚠️ Индексы легко потерять незаметно: удалить один из `__table_args__` или
добавить в запрос условие, которое не ложится на ведущую колонку. Тогда всё
продолжит работать — просто в тысячу раз медленнее. Поэтому проверяем план.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db import session_scope

#: Запросы ровно в том виде, в каком их строит `/api/catalog/ids`.
QUERIES = {
    "COUNT выборки": "SELECT COUNT(*) FROM (SELECT id FROM series WHERE is_hentai = 0)",
    "по оценке (умолчание каталога)": (
        "SELECT id FROM series WHERE is_hentai = 0 "
        "ORDER BY mal_score DESC NULLS LAST, id DESC LIMIT 50"
    ),
    "по году": (
        "SELECT id FROM series WHERE is_hentai = 0 "
        "ORDER BY year DESC NULLS LAST, id DESC LIMIT 50"
    ),
    "по названию": (
        "SELECT id FROM series WHERE is_hentai = 0 "
        "ORDER BY title ASC NULLS LAST, id DESC LIMIT 50"
    ),
    "по id": "SELECT id FROM series WHERE is_hentai = 0 ORDER BY id DESC LIMIT 50",
    "онгоинги": (
        "SELECT id FROM series WHERE is_hentai = 0 AND is_airing = 1 "
        "ORDER BY mal_score DESC NULLS LAST, id DESC LIMIT 50"
    ),
    "фильтр по типу": (
        "SELECT id FROM series WHERE is_hentai = 0 AND type = 'tv' "
        "ORDER BY mal_score DESC NULLS LAST, id DESC LIMIT 50"
    ),
}


def _plan(sql: str) -> list[str]:
    with session_scope() as session:
        return [row[-1] for row in session.execute(text("EXPLAIN QUERY PLAN " + sql)).all()]


@pytest.mark.parametrize("label,sql", list(QUERIES.items()), ids=list(QUERIES))
def test_query_uses_a_covering_index(label: str, sql: str) -> None:
    """План обязан идти по индексу, а не сканировать таблицу.

    `COVERING` означает, что до строк таблицы дело не доходит вовсе — именно это
    и превращает секунду в доли миллисекунды.
    """
    plan = _plan(sql)
    joined = " | ".join(plan)
    assert "COVERING INDEX" in joined, (
        f"«{label}» больше не покрывается индексом: {joined}. "
        "Проверьте __table_args__ у Series — на широкой таблице это секунда "
        "чтения диска на каждый запрос"
    )


def test_indexes_exist_in_the_database() -> None:
    """Индексы должны быть в БД, а не только в моделях.

    `create_all` создаёт их вместе с новой таблицей и ничего не делает с уже
    существующей — за это отвечает `add_missing_indexes()` в миграциях. Без неё
    ускорение получили бы только чистые установки.
    """
    with session_scope() as session:
        names = {
            row[1] for row in session.execute(text("PRAGMA index_list('series')")).all()
        }

    expected = {
        "ix_series_catalog_id",
        "ix_series_catalog_score",
        "ix_series_catalog_year",
        "ix_series_catalog_title",
        "ix_series_catalog_airing",
        "ix_series_catalog_type",
    }
    missing = sorted(expected - names)
    assert not missing, f"в базе нет индексов: {missing}"
