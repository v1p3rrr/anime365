"""Служебные операции над кэшем."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from app.core.permissions import Permission, require
from sqlalchemy import text

from app.core.db import session_scope
from app.services.fts import rebuild_all

log = logging.getLogger(__name__)

router = APIRouter(prefix="/maintenance", tags=["service"])


@router.post("/purge-junk", dependencies=[Depends(require(Permission.SETTINGS))])
def purge_junk() -> dict[str, Any]:
    """Удаляет пустые записи каталога, попавшие в БД до появления фильтра.

    Такие тайтлы не имеют ни одного названия и всплывали в сортировке по названию
    как «#879». Новые синхронизации их уже не пропускают, но накопленное надо убрать.
    """
    with session_scope() as session:
        ids = [
            row[0]
            for row in session.execute(
                text(
                    "SELECT id FROM series "
                    "WHERE (title IS NULL OR title = '') "
                    "  AND (all_titles IS NULL OR all_titles = '[]')"
                )
            ).all()
        ]
        if ids:
            placeholders = ", ".join(f":id{n}" for n in range(len(ids)))
            params = {f"id{n}": i for n, i in enumerate(ids)}
            session.execute(
                text(f"DELETE FROM series_fts WHERE series_id IN ({placeholders})"), params
            )
            session.execute(text(f"DELETE FROM series WHERE id IN ({placeholders})"), params)

    log.info("Удалено пустых тайтлов: %d", len(ids))
    return {"deleted": len(ids), "ids": ids[:50]}


@router.post(
    "/rebuild-search-index", dependencies=[Depends(require(Permission.SETTINGS))]
)
def rebuild_search_index() -> dict[str, Any]:
    return {"indexed": rebuild_all()}
