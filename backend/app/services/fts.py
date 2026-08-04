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


def search_ids(user_input: str, limit: int = 200) -> list[int]:
    query = build_query(user_input)
    if query is None:
        return []

    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT series_id FROM series_fts WHERE series_fts MATCH :q "
                "ORDER BY rank LIMIT :limit"
            ),
            {"q": query, "limit": limit},
        ).all()
    return [r[0] for r in rows]


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
            session.execute(
                text(f"DELETE FROM series_fts WHERE series_id IN ({placeholders})"), params
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
                    text("INSERT INTO series_fts (series_id, titles) VALUES (:sid, :titles)"),
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
