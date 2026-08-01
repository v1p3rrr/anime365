"""Эндпоинты страницы тайтла."""

from __future__ import annotations

import logging
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from app.api.deps import rate_limit
from app.core import errors
from app.core.db import session_scope
from app.core.errors import ApiError
from app.models.catalog import Series
from app.services.anime365 import Anime365Error, SubscriptionRequired
from app.services.jellyfin import jellyfin
from app.services.mapping_info import title_folder_paths
from app.services.shikimori import (
    ShikimoriError,
    get_details,
    get_relations,
    studio_logo,
)
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
        raise errors.upstream_unavailable(exc) from exc

    if data is None:
        raise ApiError(404, errors.SERIES_NOT_FOUND, "Тайтл не найден")
    return data


@router.get("/translations/{translation_id}/embed", dependencies=[_embed_limit])
async def translation_embed(
    translation_id: int,
    refresh: Annotated[bool, Query()] = False,
    with_sizes: Annotated[
        bool, Query(description="Дополнительно узнать вес файла на каждой ступени")
    ] = False,
) -> dict[str, Any]:
    """Признак вшитых субтитров, точные ступени качества и вес файла.

    Один запрос на перевод, поэтому вызывается только при раскрытии группы в UI.
    Вес стоит ещё по запросу на ступень (HEAD на CDN), поэтому за отдельным флагом.
    """
    try:
        return await get_embed(translation_id, force=refresh, with_sizes=with_sizes)
    except SubscriptionRequired as exc:
        raise ApiError(403, errors.SUBSCRIPTION_REQUIRED, str(exc), reason=str(exc)) from exc
    except Anime365Error as exc:
        raise errors.upstream_unavailable(exc) from exc


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
        raise ApiError(409, errors.SCAN_REJECTED, str(exc), reason=str(exc)) from exc
    except Anime365Error as exc:
        raise errors.upstream_unavailable(exc) from exc


@router.get(
    "/series/{series_id}/relations",
    # Запрос уходит на чужой сервис, поэтому частота ограничена. Кэш на неделю
    # снимает почти всю нагрузку, но повторные открытия страницы бывают частыми.
    dependencies=[Depends(rate_limit("relations", limit=60, window=60))],
)
async def series_relations(series_id: int, refresh: Annotated[bool, Query()] = False):
    """Порядок просмотра франшизы.

    Отдельным запросом, а не в составе страницы тайтла: источник внешний, и его
    недоступность не должна мешать открыть страницу. Пустой ответ здесь —
    нормальный исход, а не ошибка.
    """
    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            raise ApiError(404, errors.SERIES_NOT_FOUND, "Тайтл не найден")
        mal_id = series.mal_id

    if not mal_id:
        # Без mal_id спрашивать нечего: связи приходят в его терминах.
        return {"nodes": [], "available": False}

    try:
        data = await get_relations(mal_id, force=refresh)
    except (ShikimoriError, httpx.HTTPError) as exc:
        raise ApiError(
            502, errors.RELATIONS_UNAVAILABLE, f"Шикимори недоступен: {exc}", reason=str(exc)
        ) from exc

    return {**data, "available": True}


@router.get(
    "/series/{series_id}/details",
    dependencies=[Depends(rate_limit("shiki_details", limit=60, window=60))],
)
async def series_details(series_id: int, refresh: Annotated[bool, Query()] = False):
    """Студия, режиссёр и состояние выхода — то, чего нет в API Anime365.

    Отдельным запросом по той же причине, что и связи: источник внешний, и его
    недоступность не должна мешать открыть страницу. Отсутствие данных здесь
    нормальный исход — отвечаем `available: false`, а не ошибкой.
    """
    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            raise ApiError(404, errors.SERIES_NOT_FOUND, "Тайтл не найден")
        mal_id = series.mal_id

    if not mal_id:
        return {"available": False}

    data = await get_details(mal_id, force=refresh)
    if data is None:
        return {"available": False}
    return {**data, "available": True}


@router.get(
    "/shikimori/studio/{studio_id}",
    # Картинка маленькая и запрашивается по одной на тайтл, но эндпоинт ходит
    # в чужой сервис — без ограничителя его можно было бы использовать как
    # генератор нагрузки на Шикимори.
    dependencies=[Depends(rate_limit("studio_logo", limit=120, window=60))],
)
async def studio_logo_image(studio_id: int) -> Response:
    """Логотип студии со своего домена.

    Прямая ссылка на Шикимори не годится: CSP разрешает картинки только со
    своего домена и зеркал Anime365, а прокси для обхода блокировок есть у
    сервера, но не у браузера.
    """
    result = await studio_logo(studio_id)
    if result is None:
        raise ApiError(404, errors.SERIES_NOT_FOUND, "Логотип недоступен")
    content, content_type = result
    # Логотипы студий не меняются годами — пусть браузер не спрашивает заново.
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.get(
    "/series/{series_id}/jellyfin",
    # Первый вызов обходит библиотеку Jellyfin, дальше пять минут отвечает из
    # памяти. Ограничение здесь не про нагрузку на нас, а про чужой сервис.
    dependencies=[Depends(rate_limit("jellyfin_link", limit=60, window=60))],
)
async def series_jellyfin_link(series_id: int) -> dict[str, Any]:
    """Ссылка на страницу тайтла в Jellyfin.

    Отдельным запросом по той же причине, что и связи: Jellyfin — внешний
    сервис, и его недоступность не должна мешать открыть страницу тайтла.

    Отсутствие ссылки — **нормальный исход**, а не ошибка. Так выглядит и
    выключенная интеграция, и тайтл, которого у Jellyfin нет. Различать эти
    случаи в ответе стоит: «выключено» чинится в настройках, «не найден» —
    сканированием библиотеки, и подсказать это можно только зная разницу.
    """
    if not jellyfin.turned_on:
        return {"available": False, "reason": "disabled"}

    if not jellyfin.configured:
        # Тумблер включён, а адреса или ключа нет. Прятать кнопку здесь нельзя:
        # пользователь только что включил интеграцию и ждёт результата, а
        # молчание он прочитает как «не работает», а не как «доскажи настройки».
        return {
            "available": False,
            "reason": "not_configured",
            "missing": jellyfin.missing_settings(),
        }

    rel_paths = title_folder_paths(series_id)
    if not rel_paths:
        # Тайтл не привязан ни к одной папке — искать в Jellyfin нечего.
        return {"available": False, "reason": "not_mapped"}

    found = await jellyfin.resolve_title(rel_paths)
    if not found:
        return {"available": False, "reason": "not_found"}

    return {"available": True, **found}


@router.get("/series/{series_id}/audio")
def series_audio(series_id: int) -> dict[str, Any]:
    """Извлечённые звуковые дорожки этого тайтла."""
    from app.services.audio_library import tracks_for_series

    return {"tracks": tracks_for_series(series_id)}


class TreatAsMovieRequest(BaseModel):
    # None возвращает автоопределение по типу из каталога.
    treat_as_movie: bool | None = None


@router.put("/series/{series_id}/treat-as-movie")
def set_treat_as_movie(series_id: int, payload: TreatAsMovieRequest) -> dict[str, Any]:
    """Считать тайтл фильмом или сериалом.

    Автоматически развести это нельзя: в каталоге есть тайтлы с `type=movie` и
    несколькими эпизодами — связка из двух-трёх фильмов. Radarr фильмом такое не
    считает, и класть её надо к сериалам. Решение остаётся за пользователем, а
    от него зависит корень, в который уедут загрузки.
    """
    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            raise ApiError(404, errors.SERIES_NOT_FOUND, "Тайтл не найден")
        series.treat_as_movie = payload.treat_as_movie
        effective = (
            payload.treat_as_movie
            if payload.treat_as_movie is not None
            else series.type == "movie"
        )
    return {"treat_as_movie": payload.treat_as_movie, "effective_is_movie": effective}
