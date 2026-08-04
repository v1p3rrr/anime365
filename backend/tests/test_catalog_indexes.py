"""Запросы каталога обязаны отвечаться по покрывающему индексу, а не чтением таблицы.

Почему это отдельный тест, а не «и так видно». Таблица `series` широкая: в
каждой строке лежат `titles`, `all_titles`, `descriptions` и `links` — вместе
около 25 МБ на 29 тысяч строк. Любое обращение к самой строке читает эту массу,
даже когда нужен один `id`. Замерено в контейнере на настоящем каталоге:

| запрос | без покрытия | с покрытием |
|---|---|---|
| `COUNT` по фильтру сезона | 3675 мс | 1,3 мс |
| годы + сортировка по оценке | 661 мс | 0,2 мс |
| оценка от 7 + сортировка по названию | 850 мс | 0,3 мс |
| фильтр по типу + сортировка по году | 158 мс | 0,3 мс |

⚠️ **Одного `COVERING` где-нибудь в плане мало — это выяснилось дорого.**
Первая редакция теста проверяла, что слово встречается в плане хоть раз, и
пропускала главное: сочетания «фильтр по одному полю, сортировка по другому».
Там индекс покрывал что-то одно, а недостающую колонку SQLite дочитывал из
таблицы. Поэтому теперь требуется, чтобы **каждое** обращение к `series` было
покрывающим, и перебираются сочетания фильтров с сортировками
([D-84](../../claude/09-decisions.md)).

⚠️ И проверяются **настоящие** запросы: SQL перехватывается прямо из
`/api/catalog/ids`, а не пишется в тесте руками. Копия запроса разошлась бы с
оригиналом, и тест продолжал бы радостно проверять то, чего в коде уже нет.
"""

from __future__ import annotations

import re
from contextlib import contextmanager

import pytest
from sqlalchemy import event, inspect, text

from app.api import catalog as cat
from app.core.db import engine, session_scope
from app.models.catalog import Series

#: Фильтры, доступные в интерфейсе, — как параметры эндпоинта.
_FILTERS: dict[str, dict] = {
    "без фильтра": {},
    "тип": {"type": "tv"},
    "год от и до": {"year_from": 2020, "year_to": 2024},
    "сезон": {"season": "Лето"},
    "онгоинги": {"is_airing": True},
    "оценка от": {"score_from": 7},
    "жанр": {"genres": [8]},
    "два жанра И": {"genres": [8, 35], "genres_and": True},
    "только в библиотеке": {"local": "only"},
    "нет в библиотеке": {"local": "missing"},
    "привязка под вопросом": {"mapping_problem": True},
    "есть перевод на сайте": {"has": ["sub_ja"]},
    "с хентаем": {"include_hentai": True},
    "всё сразу": {
        "type": "tv",
        "year_from": 2015,
        "score_from": 6,
        "genres": [8],
    },
}

_SORTS = ["id", "score", "year", "title"]

CASES = [
    (f"{name} + {sort}", {**params, "sort": sort})
    for sort in _SORTS
    for name, params in _FILTERS.items()
]
# Глубокая страница: окно берётся не с начала выборки.
CASES += [
    (f"глубокая страница + {sort}", {"sort": sort, "offset": 20000, "limit": 200})
    for sort in _SORTS
]

#: Шаг плана, в котором участвует именно `series`, а не `series_genre`/`series_fts`.
_SERIES_STEP = re.compile(r"\b(?:SEARCH|SCAN)\s+series\b")


@contextmanager
def _captured():
    """Собирает SQL, который эндпоинт реально отправил в базу."""
    recorded: list[tuple[str, object]] = []

    def before(_conn, _cursor, statement, parameters, _context, _many):
        recorded.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield recorded
    finally:
        event.remove(engine, "before_cursor_execute", before)


def _plan(sql: str, params) -> list[str]:
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("EXPLAIN QUERY PLAN " + sql, params or ())
        rows = cursor.fetchall()
        cursor.close()
    finally:
        raw.close()
    return [row[-1] for row in rows]


@pytest.mark.parametrize("label,params", CASES, ids=[c[0] for c in CASES])
def test_every_touch_of_series_is_covered_by_an_index(label: str, params: dict) -> None:
    """Ни один шаг плана не должен доходить до строк широкой таблицы.

    `COVERING` означает, что всё нужное лежит в самом индексе. Как только слово
    пропадает, запрос начинает дочитывать колонку из таблицы — и это разница
    между долей миллисекунды и секундами.
    """
    with _captured() as recorded:
        cat.catalog_ids(**params)

    touched = False
    for sql, values in recorded:
        plan = _plan(sql, values)
        for step in plan:
            if not _SERIES_STEP.search(step):
                continue
            touched = True
            assert "COVERING INDEX" in step, (
                f"«{label}» больше не покрывается индексом: {step}. "
                "Проверьте __table_args__ у Series и SORT_INDEX в api/catalog.py: "
                "в индексе с нужным порядком сортировки должны лежать и все "
                "фильтруемые колонки, иначе SQLite дочитывает их из широкой таблицы"
            )

    assert touched, f"«{label}»: ни один запрос не обратился к series"


def test_hint_names_an_index_that_exists() -> None:
    """`INDEXED BY` — жёсткое требование: нет индекса, нет и запроса.

    Планировщик без подсказки просто выбрал бы что-то другое и стал медленнее,
    а с подсказкой SQLite отвечает ошибкой. Поэтому имена из `SORT_INDEX`
    обязаны существовать в базе, а не только в модели.
    """
    with session_scope() as session:
        names = {row[1] for row in session.execute(text("PRAGMA index_list('series')")).all()}

    wanted = set(cat.SORT_INDEX.values()) | {cat.COUNT_INDEX}
    missing = sorted(wanted - names)
    assert not missing, f"в базе нет индексов, названных в подсказках: {missing}"


def test_index_definitions_match_the_models() -> None:
    """Набор колонок в базе обязан совпадать с объявленным в модели.

    Ловушка, на которую наступили: индекс переопределили под тем же именем, а
    в базе остался старый — проверка «есть индекс с таким именем» его
    пропускала. Всё продолжало работать, просто медленно.
    """
    actual = {
        idx["name"]: list(idx["column_names"]) for idx in inspect(engine).get_indexes("series")
    }
    for index in Series.__table__.indexes:
        wanted = [c.name for c in index.columns]
        assert actual.get(index.name) == wanted, (
            f"индекс {index.name} в базе объявлен как {actual.get(index.name)}, "
            f"а модель требует {wanted}"
        )


def test_obsolete_indexes_are_gone() -> None:
    """Лишний индекс — не просто занятое место.

    `ix_series_catalog_airing` и `ix_series_catalog_type` покрывали фильтр, но
    не сортировку. Планировщик при равной оценке стоимости выбирал их и уходил
    дочитывать колонку из широкой таблицы — то есть присутствие этих индексов
    делало запросы медленнее, а не быстрее.
    """
    from app.core.migrations import OBSOLETE_INDEXES

    with session_scope() as session:
        names = {row[1] for row in session.execute(text("PRAGMA index_list('series')")).all()}

    left = sorted(set(OBSOLETE_INDEXES["series"]) & names)
    assert not left, f"устаревшие индексы остались в базе: {left}"
