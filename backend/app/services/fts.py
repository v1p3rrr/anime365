"""Полнотекстовый поиск по названиям (SQLite FTS5).

Индексируем all_titles целиком, а не одно название: titles.en заполнен лишь
у 13 412 тайтлов из 29 231, тогда как all_titles есть у всех и содержит в среднем
7.6 вариантов — русский, romaji, японский и альтернативные написания.

БЕЗОПАСНОСТЬ: пользовательская строка никогда не склеивается в MATCH-выражение.
Операторы FTS5 (кавычки, *, NEAR, OR, AND, ^) в вводе ломают запрос или дают
неожиданную выборку, поэтому ввод разбирается на токены и собирается заново.
"""

from __future__ import annotations

import logging
import re
import time

from sqlalchemy import text

from app.core.db import engine, session_scope
from app.models.catalog import Series

log = logging.getLogger(__name__)

#: Сколько тайтлов пересобирать в одной транзакции.
#:
#: Не про производительность, а про то, надолго ли берётся writer-lock. Пятьсот
#: записей — доли секунды; всё, что не влезло, подождёт следующей порции и не
#: помешает ни загрузкам, ни интерфейсу.
FTS_CHUNK = 500

#: Пауза между порциями.
#:
#: Без неё разбиение помогает вдвое меньше, чем могло бы: освободив лок, поток
#: тут же забирает его обратно, и сосед, ждущий своей очереди, всё равно стоит
#: секундами. Замерено на 29 214 тайтлах — худшее ожидание короткой записи
#: было 5,1 с без паузы и 0,2 с с ней. Десять миллисекунд на порцию добавляют к
#: проходу меньше секунды, а проход этот фоновый, и его никто не ждёт.
FTS_CHUNK_PAUSE = 0.01

#: ⚠️ `rowid` записи — это и есть `series.id`, и на этом держится скорость.
#:
#: Колонка `series_id` осталась, но читать её дорого: у FTS5 неиндексируемая
#: колонка лежит в содержимом записи, и достать её можно, только развернув саму
#: запись. Замерено на частом слове (7525 совпадений в контейнере):
#:
#: | что выбираем | цена |
#: |---|---|
#: | `COUNT(*)` | 1,5 мс |
#: | `rowid` | **2,4 мс** |
#: | `series_id` | **243 мс** |
#: | `series_id ORDER BY rank LIMIT 5000` | **459 мс** |
#:
#: Поэтому идентификаторы берутся из `rowid`, а сама колонка нужна только для
#: того, чтобы понять, что база собрана по-старому, и пересобрать её.
CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS series_fts USING fts5(
    titles,
    series_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
)
"""

# Всё, что не буква и не цифра, считаем разделителем: так из ввода физически
# не может выжить ни один оператор FTS5.
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def init_fts() -> None:
    with engine.begin() as conn:
        conn.execute(text(CREATE_FTS))


def build_query(user_input: str) -> str | None:
    """Превращает пользовательский ввод в безопасное MATCH-выражение.

    Каждый токен оборачивается в кавычки (внутренние кавычки удвоены по правилам FTS5)
    и получает префиксный поиск, чтобы «фрир» находило «Фрирен».
    """
    tokens = _TOKEN_RE.findall(user_input or "")
    if not tokens:
        return None

    # Ограничение на всякий случай: гигантская строка не должна превращаться
    # в сотню AND-условий.
    parts = []
    for token in tokens[:12]:
        escaped = token.replace('"', '""')
        parts.append(f'"{escaped}"*')
    return " AND ".join(parts)


def matching_ids(user_input: str) -> list[int] | None:
    """Все совпадения — без ранжирования и без ограничения.

    ⚠️ **Ни того, ни другого здесь быть не должно, и это замерено.**

    Прежняя редакция брала пять тысяч лучших по релевантности
    (`ORDER BY rank LIMIT 5000`). Но выдачу каталога сортирует сам пользователь
    — по оценке, году или названию, — то есть ранг решал единственное: какие
    совпадения молча выбросить. А стоил он дорого: FTS5 считает BM25 по **всем**
    совпадениям, а не по первым `limit`. На частом слове (7525 совпадений):
    просто найти их — 2 мс, отсортировать по релевантности — **459 мс**.

    Возвращает None, если искать нечего (пустая строка, одни знаки препинания).
    """
    query = build_query(user_input)
    if query is None:
        return None

    with session_scope() as session:
        rows = session.execute(
            text("SELECT rowid FROM series_fts WHERE series_fts MATCH :q"),
            {"q": query},
        ).all()
    return [r[0] for r in rows]


def match_condition(user_input: str):
    """Условие «идентификатор нашёлся поиском», готовое к подстановке в фильтры.

    Возвращает None, когда искать нечего или ничего не нашлось, — вызывающий
    решает сам, что это значит (для каталога — пустая выдача).

    ⚠️ Числа подставляются в текст запроса, а не передаются параметрами, и это
    осознанно. Замерено на частом слове (7525 совпадений):

    | как передаём совпадения | цена |
    |---|---|
    | параметрами через `IN (…)` у SQLAlchemy | **900 мс** на сборку выражения |
    | подзапросом `id IN (SELECT … MATCH …)` | 180–390 мс, SQLite прогоняет поиск заново на каждый из двух запросов |
    | готовыми числами в тексте | **0,6 мс** |

    Первый вариант дорог мимо базы: тысячи параметров собираются дважды, потому
    что рядом с окном идёт `COUNT`.

    Инъекции здесь нет: в текст попадают только значения `series_id`, взятые из
    нашей же таблицы и приведённые к `int`. Строка пользователя в SQL не
    склеивается никогда — она уходит связанным параметром после `build_query()`.
    """
    ids = matching_ids(user_input)
    if not ids:
        return None
    return text("series.id IN (" + ",".join(str(int(i)) for i in ids) + ")")


def rebuild_fts_for(series_ids: list[int]) -> None:
    """Пересобирает записи индекса для указанных тайтлов.

    ⚠️ **Транзакция на порцию, а не на весь список.** Раньше здесь была одна
    `session_scope()` на всё, и после полного синка она держала writer-lock
    **36,5 секунды** на 29 214 тайтлах — замерено в контейнере. SQLite
    допускает одного писателя, `busy_timeout` у нас 10 секунд, поэтому всё
    остальное в это время получало «database is locked»: и запись прогресса
    загрузки, и сохранение курсора синка. Ровно это и было в логах на сервере.

    Порциями работа идёт те же 36 секунд, но лок отпускается 58 раз, и между
    порциями успевают отработать и загрузки, и интерфейс. Атомарность целого
    индекса нам не нужна: он полностью выводится из таблицы `series` и в худшем
    случае пересобирается заново.
    """
    if not series_ids:
        return

    for start in range(0, len(series_ids), FTS_CHUNK):
        chunk = series_ids[start : start + FTS_CHUNK]
        # Именованные параметры под каждый элемент: значения не попадают в текст SQL.
        placeholders = ", ".join(f":id{n}" for n in range(len(chunk)))
        params = {f"id{n}": int(sid) for n, sid in enumerate(chunk)}

        with session_scope() as session:
            # По `rowid` — потому что он и есть идентификатор тайтла. Условие по
            # `series_id` оставлено на случай базы, собранной до этого правила:
            # там `rowid` произвольный, и без второго условия старая запись
            # осталась бы рядом с новой.
            session.execute(
                text(
                    f"DELETE FROM series_fts WHERE rowid IN ({placeholders}) "
                    f"OR series_id IN ({placeholders})"
                ),
                params,
            )

            rows = session.execute(
                text(f"SELECT id, titles, all_titles FROM series WHERE id IN ({placeholders})"),
                params,
            ).all()

            payload = [
                {"sid": series_id, "titles": _flatten_titles(titles_json, all_titles_json)}
                for series_id, titles_json, all_titles_json in rows
            ]
            if payload:
                session.execute(
                    text(
                        "INSERT INTO series_fts (rowid, series_id, titles) "
                        "VALUES (:sid, :sid, :titles)"
                    ),
                    payload,
                )

        # Отдаём лок соседям. Без этой паузы поток забирает его обратно
        # мгновенно, и ждущая короткая запись стоит секундами (замер: 5,1 с).
        time.sleep(FTS_CHUNK_PAUSE)


def rebuild_all() -> int:
    with session_scope() as session:
        ids = [r[0] for r in session.execute(text("SELECT id FROM series")).all()]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM series_fts"))
    rebuild_fts_for(ids)
    return len(ids)


def needs_rowid_rebuild() -> bool:
    """Собран ли индекс до правила «rowid — это series_id».

    У таких баз `rowid` раздавался автоинкрементом, и поиск по нему вернул бы
    чужие тайтлы. Проверка стоит один запрос: достаточно найти одну строку с
    расхождением, а не пересчитывать все.
    """
    with session_scope() as session:
        mismatched = session.execute(
            text("SELECT 1 FROM series_fts WHERE rowid <> series_id LIMIT 1")
        ).first()
    return mismatched is not None


def _flatten_titles(titles_json: str | dict | None, all_titles_json: str | list | None) -> str:
    import json

    values: list[str] = []

    def _add(source):
        if isinstance(source, str):
            try:
                source = json.loads(source)
            except (ValueError, TypeError):
                return
        if isinstance(source, dict):
            values.extend(str(v) for v in source.values() if v)
        elif isinstance(source, list):
            values.extend(str(v) for v in source if v)

    _add(titles_json)
    _add(all_titles_json)

    # Дубликаты убираем, порядок не важен — FTS всё равно токенизирует.
    return " ".join(dict.fromkeys(values))


def _unused(_: Series) -> None:  # pragma: no cover
    """Держит импорт модели, чтобы таблица создавалась до инициализации FTS."""
