"""Эндпоинты страницы тайтла."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from app.services.anime365 import Anime365Error, SubscriptionRequired
from app.services.title_service import build_title_page, get_embed

log = logging.getLogger(__name__)

router = APIRouter(tags=["title"])


@router.get("/series/{series_id}")
async def title_page(
    series_id: int,
    refresh: Annotated[bool, Query(description="Перезапросить переводы, минуя кэш")] = False,
) -> dict[str, Any]:
    try:
        data = await build_title_page(series_id, force_refresh=refresh)
    except Anime365Error as exc:
        raise HTTPException(502, f"Anime365 недоступен: {exc}") from exc

    if data is None:
        raise HTTPException(404, "Тайтл не найден")
    return data


@router.get("/translations/{translation_id}/embed")
async def translation_embed(
    translation_id: int,
    refresh: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """Признак вшитых субтитров и точные ступени качества.

    Один запрос на перевод, поэтому вызывается только при раскрытии группы в UI.
    """
    try:
        return await get_embed(translation_id, force=refresh)
    except SubscriptionRequired as exc:
        raise HTTPException(403, str(exc)) from exc
    except Anime365Error as exc:
        raise HTTPException(502, f"Anime365 недоступен: {exc}") from exc
