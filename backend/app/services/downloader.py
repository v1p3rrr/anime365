"""Скачивание файлов с докачкой по Range.

Ключевые особенности, вытекающие из поведения API (см. claude/02-api-findings.md):

  * ссылки подписанные и короткоживущие, поэтому embed запрашивается заново
    непосредственно перед скачиванием, а не берётся из кэша;
  * видео отдаётся через 302 на CDN, субтитры — прямой ссылкой без токена;
  * при обрыве продолжаем с того же места по Range, а не качаем заново:
    серия в 1080p весит около 230 МБ, терять их обидно.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from app.core.config import settings
from app.services.anime365 import Anime365Client, Anime365Error, SubscriptionRequired

log = logging.getLogger(__name__)

CHUNK_SIZE = 1 << 20  # 1 МБ

# Хосты CDN, на которые допустимо уходить по редиректу. Проверка нужна,
# чтобы подменённый ответ не увёл загрузку на посторонний сервер.
ALLOWED_CDN_SUFFIXES = (".quantum-phantom-moon.ru", ".b-cdn.net")


class DownloadCancelled(Exception):
    """Задачу отменили или поставили на паузу — это не ошибка."""


@dataclass
class Progress:
    bytes_done: int
    bytes_total: int
    speed_bps: float


ProgressCallback = Callable[[Progress], None]


def is_allowed_target(url: str) -> bool:
    """Проверяет, что ссылка ведёт на известный хост.

    Зеркала и CDN известны заранее; всё остальное отвергаем, чтобы подменённый
    ответ не заставил приложение качать с постороннего сервера.
    """
    from urllib.parse import urlsplit

    host = urlsplit(url).netloc.split(":")[0].lower()
    if not host:
        return False

    known_hosts = {urlsplit(o).netloc.lower() for o in settings.allowed_origins()}
    if host in known_hosts:
        return True
    return any(host.endswith(suffix) for suffix in ALLOWED_CDN_SUFFIXES)


# Что считаем допустимым содержимым для каждого вида загрузки.
EXPECTED_CONTENT = {
    "video": ("video/", "application/octet-stream", "application/mp4"),
    "subtitle": ("text/", "application/octet-stream", "application/x-subrip"),
}

# Минимальный правдоподобный размер. HTML-страница отказа весит порядка сотни
# килобайт, поэтому одного размера мало — но как дополнительный сигнал полезно.
MIN_VIDEO_BYTES = 1 << 20  # 1 МБ


def _ensure_expected_content(response: httpx.Response, expect: str, url: str) -> None:
    """Проверяет, что нам отдали файл, а не страницу с ошибкой.

    Сайт умеет отвечать HTML со статусом 200 — например, если в ссылке нет
    access_token. Без этой проверки страница сохранялась бы как .mp4.
    """
    content_type = response.headers.get("content-type", "").lower().split(";")[0].strip()
    if not content_type:
        return

    allowed = EXPECTED_CONTENT.get(expect, ())
    if any(content_type.startswith(prefix) for prefix in allowed):
        return

    if content_type.startswith("text/html"):
        raise Anime365Error(
            "Вместо файла пришла HTML-страница. Обычно это значит, что ссылка "
            "требует access_token или подписка неактивна"
        )
    raise Anime365Error(f"Неожиданный тип содержимого {content_type!r} при скачивании {expect}")


class Downloader:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Downloader:
        limit = settings.get_int("speed_limit_kbps")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0, read=120.0),
            follow_redirects=True,
            headers={"User-Agent": settings.get_str("user_agent")},
        )
        self._speed_limit_bps = limit * 1024 if limit > 0 else 0
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def resolve_links(self, translation_id: int) -> dict:
        """Свежие ссылки на видео и субтитры.

        Обязательно перед каждой загрузкой: подписи в CDN-ссылках живут недолго,
        поэтому кэшировать их бессмысленно.
        """
        async with Anime365Client() as client:
            info = await client.embed(translation_id)
        return {
            "download": info.download,
            "subtitles_url": info.subtitles_url,
            "heights": info.heights,
        }

    async def fetch(
        self,
        url: str,
        destination: Path,
        on_progress: ProgressCallback | None = None,
        should_stop: Callable[[], bool] | None = None,
        expect: str = "video",
    ) -> int:
        """Качает файл, продолжая с уже скачанного места.

        Возвращает итоговый размер. Файл пишется во временный `.part`, чтобы
        оборванная загрузка не выглядела как готовый файл.
        """
        assert self._client is not None, "используйте загрузчик через async with"

        if not is_allowed_target(url):
            raise Anime365Error(f"Ссылка ведёт на неизвестный хост: {url[:80]}")

        part = destination.with_suffix(destination.suffix + ".part")
        part.parent.mkdir(parents=True, exist_ok=True)

        existing = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}

        started = time.monotonic()
        downloaded_now = 0

        async with self._client.stream("GET", url, headers=headers) as response:
            if response.status_code == 403:
                raise SubscriptionRequired(
                    "403 при скачивании: проверьте access_token и активность подписки"
                )
            if existing and response.status_code == 200:
                # Сервер не поддержал докачку — начинаем заново, иначе получим
                # склейку начала файла с его серединой.
                log.info("Докачка не поддержана, качаю %s заново", destination.name)
                existing = 0
                part.unlink(missing_ok=True)
            elif existing and response.status_code != 206:
                raise Anime365Error(f"Неожиданный ответ на докачку: {response.status_code}")
            elif not existing and response.status_code != 200:
                raise Anime365Error(f"HTTP {response.status_code} при скачивании")

            # ⚠️ Обязательная проверка: сайт умеет отвечать HTML-страницей со
            # статусом 200 вместо файла (например, без access_token). Без этой
            # проверки страница молча сохранялась бы как .mp4 — битый файл,
            # который выглядит скачанным и попадает в библиотеку.
            _ensure_expected_content(response, expect, url)

            total = existing + int(response.headers.get("content-length", 0))
            mode = "ab" if existing else "wb"

            with part.open(mode) as handle:
                async for chunk in response.aiter_bytes(CHUNK_SIZE):
                    if should_stop is not None and should_stop():
                        raise DownloadCancelled()

                    handle.write(chunk)
                    existing += len(chunk)
                    downloaded_now += len(chunk)

                    elapsed = max(time.monotonic() - started, 1e-6)
                    speed = downloaded_now / elapsed
                    if on_progress is not None:
                        on_progress(Progress(existing, total, speed))

                    if self._speed_limit_bps:
                        await self._throttle(downloaded_now, started)

        # Готовый файл появляется одним движением: до этого момента на диске
        # лежит .part, который сканер библиотеки игнорирует.
        part.replace(destination)
        return destination.stat().st_size

    async def _throttle(self, downloaded: int, started: float) -> None:
        expected = downloaded / self._speed_limit_bps
        actual = time.monotonic() - started
        if expected > actual:
            await asyncio.sleep(expected - actual)


def pick_download_url(links: dict, preferred_height: int) -> tuple[str | None, int | None]:
    """Выбирает ссылку под нужное качество.

    Максимальная ступень не выше желаемой, иначе максимальная из доступных ниже.
    4K скачать нельзя в принципе — потолок ступеней 1080.
    """
    entries = links.get("download") or []
    if not entries:
        return None, None

    by_height = {int(item["height"]): item["url"] for item in entries if item.get("height")}
    if not by_height:
        return None, None

    suitable = [h for h in by_height if h <= preferred_height]
    height = max(suitable) if suitable else max(by_height)
    return by_height[height], height


def free_space_bytes(path: Path) -> int:
    import shutil

    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0
