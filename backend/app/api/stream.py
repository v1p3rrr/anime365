"""Проксирование видео и субтитров для встроенного плеера.

⚠️ Ключевое ограничение: **access_token не должен попасть в браузер.**

Ссылки скачивания содержат токен в query-параметре. Отдать такую ссылку в
`<video src=...>` означало бы показать бессрочный премиум-токен в исходниках
страницы, в истории браузера и в логах любого прокси по пути. Поэтому браузер
обращается сюда, а токен подставляется на сервере.

Второе: это **не** открытый прокси. Эндпоинт принимает идентификатор перевода,
а не URL. Ссылку он получает сам, через `embed`, и дополнительно сверяет хост
с allowlist — подменённый ответ API не заставит проксировать что попало.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from app.api.deps import rate_limit
from app.core.config import settings
from app.services.anime365 import Anime365Client, Anime365Error, SubscriptionRequired
from app.services.downloader import is_allowed_target
from app.services.title_service import get_embed

log = logging.getLogger(__name__)

router = APIRouter(prefix="/stream", tags=["stream"])

# Заголовки, которые имеет смысл передать наверх от плеера к CDN.
# Range обязателен: без него перемотка не работает, а браузер тянет весь файл.
FORWARDED_REQUEST_HEADERS = ("range",)

# Что отдаём обратно плееру. Остальное (куки, заголовки CDN) не нужно и может
# нести лишние сведения о нашем окружении.
FORWARDED_RESPONSE_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
)

CHUNK_SIZE = 1 << 18  # 256 КБ — достаточно для плавной отдачи, не раздувает память


def _pick_stream_url(embed_stream: list[dict[str, Any]], height: int | None) -> str | None:
    """Ссылка на поток нужного качества.

    Берём максимальную ступень не выше запрошенной — то же правило, что и при
    скачивании: если ровно такой нет, лучше отдать что есть ниже, чем отказать.
    """
    by_height: dict[int, str] = {}
    for item in embed_stream or []:
        urls = item.get("urls") or []
        item_height = item.get("height")
        if urls and item_height:
            by_height[int(item_height)] = urls[0]

    if not by_height:
        return None
    if height is None:
        return by_height[max(by_height)]

    suitable = [h for h in by_height if h <= height]
    return by_height[max(suitable) if suitable else min(by_height)]


@router.get(
    "/{translation_id}/video",
    # Плеер открывает поток один раз и потом тянет диапазоны по тому же URL,
    # но каждый новый запрос — это обращение к embed. Ограничиваем частоту.
    dependencies=[Depends(rate_limit("stream", limit=60, window=60))],
)
async def stream_video(
    translation_id: int,
    height: Annotated[int | None, Query(description="Желаемая высота, максимум из доступных ниже")] = None,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> StreamingResponse:
    """Отдаёт видео перевода, подставляя токен на сервере."""
    try:
        async with Anime365Client() as client:
            info = await client.embed(translation_id)
    except SubscriptionRequired as exc:
        raise HTTPException(403, str(exc)) from exc
    except Anime365Error as exc:
        raise HTTPException(502, f"Anime365 недоступен: {exc}") from exc

    url = _pick_stream_url(info.stream, height)
    if not url:
        # У части переводов потоковых ссылок нет — тогда играем то же, что качаем.
        for item in info.download:
            if item.get("url"):
                url = item["url"]
                break

    if not url:
        raise HTTPException(404, "У этого перевода нет ссылок для воспроизведения")

    if not is_allowed_target(url):
        # Ссылку выдал API, но доверять ей вслепую нельзя: подменённый ответ
        # превратил бы эндпоинт в открытый прокси на произвольный хост.
        log.error("Отклонён посторонний хост при воспроизведении: %s", url)
        raise HTTPException(502, "Ссылка ведёт на неизвестный хост")

    headers = {"User-Agent": settings.get_str("user_agent")}
    if range_header:
        headers["Range"] = range_header

    client = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=None))
    try:
        request = client.build_request("GET", url, headers=headers)
        upstream = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        detail = str(exc) or type(exc).__name__
        raise HTTPException(502, f"Не удалось открыть поток: {detail}") from exc

    if upstream.status_code >= 400:
        status = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(502, f"CDN ответил {status}")

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(CHUNK_SIZE):
                yield chunk
        finally:
            # Соединения закрываем всегда: брошенная вкладка не должна
            # оставлять висящий сокет к CDN.
            await upstream.aclose()
            await client.aclose()

    passthrough = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() in FORWARDED_RESPONSE_HEADERS
    }
    return StreamingResponse(body(), status_code=upstream.status_code, headers=passthrough)


@router.get("/{translation_id}/subtitles")
async def stream_subtitles(translation_id: int) -> Response:
    """Файл субтитров перевода.

    Токен для субтитров не нужен, но проксируем их всё равно: иначе браузер
    пошёл бы на сторонний домен, что запрещено нашей же CSP (`connect-src 'self'`).
    """
    try:
        embed = await get_embed(translation_id)
    except SubscriptionRequired as exc:
        raise HTTPException(403, str(exc)) from exc
    except Anime365Error as exc:
        raise HTTPException(502, f"Anime365 недоступен: {exc}") from exc

    if not embed.get("has_subtitles"):
        raise HTTPException(404, "У этого перевода субтитры вшиты в видео, отдельного файла нет")

    async with Anime365Client() as client:
        info = await client.embed(translation_id)

    url = info.subtitles_url
    if not url or not is_allowed_target(url):
        raise HTTPException(404, "Ссылка на субтитры недоступна")

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client_http:
        try:
            response = await client_http.get(
                url, headers={"User-Agent": settings.get_str("user_agent")}
            )
        except httpx.HTTPError as exc:
            detail = str(exc) or type(exc).__name__
            raise HTTPException(502, f"Не удалось получить субтитры: {detail}") from exc

    if response.status_code >= 400:
        raise HTTPException(502, f"Сервер субтитров ответил {response.status_code}")

    # ASS отдаём как текст: разбирать его будет плеер на стороне браузера.
    return Response(
        response.content,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "private, max-age=3600"},
    )
