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

from sqlalchemy import text

from app.core.db import engine, session_scope
from app.models.catalog import Series

log = logging.getLogger(__name__)

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
    """Пересобирает записи индекса для указанных тайтлов."""
    if not series_ids:
        return

    chunk_size = 500
    with session_scope() as session:
        for start in range(0, len(series_ids), chunk_size):
            chunk = series_ids[start : start + chunk_size]
            # Именованные параметры под каждый элемент: значения не попадают в текст SQL.
            placeholders = ", ".join(f":id{n}" for n in range(len(chunk)))
            params = {f"id{n}": int(sid) for n, sid in enumerate(chunk)}

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
