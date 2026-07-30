"""Эндпоинты страницы тайтла."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import rate_limit
from app.services.anime365 import Anime365Error, SubscriptionRequired
from app.services.title_service import build_title_page, get_embed, quality_refiner

log = logging.getLogger(__name__)

router = APIRouter(tags=["title"])

# embed — один запрос к Anime365 на перевод. При раскрытии больших групп их
# летит много подряд, но 120 в минуту с запасом покрывают любое ручное раскрытие.
_embed_limit = Depends(rate_limit("embed", limit=120, window=60))


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


@router.get("/translations/{translation_id}/embed", dependencies=[_embed_limit])
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


@router.post(
    "/series/{series_id}/refine-quality",
    # Один запрос к Anime365 на серию: у длинного тайтла это сотни обращений,
    # и повторять их подряд незачем.
    dependencies=[Depends(rate_limit("refine-quality", limit=5, window=60))],
)
async def refine_quality(series_id: int) -> dict[str, Any]:
    """Заменяет оценку качества точными ступенями скачивания.

    Оценка строится по разрешению исходника, а оно не определяет набор ступеней:
    у источника 528p встречается [720, 360], у 4K потолок всё равно 1080. Точные
    данные есть только в embed — по одному запросу на серию.
    """
    try:
        return await quality_refiner.run(series_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Anime365Error as exc:
        raise HTTPException(502, f"Anime365 недоступен: {exc}") from exc
