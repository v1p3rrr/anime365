"""Клиент Anime365 API — единственная точка выхода в сеть.

Здесь собрано всё, что выяснено о поведении живого API (подробности в claude/02-api-findings.md):
  * `fields` обязателен, иначе список тайтлов тянет за собой эпизоды и описания;
  * полное сканирование только через `afterId`, `offset` на больших списках медленный;
  * пагинация переводов работает ТОЛЬКО с `feed=id`;
  * `embed` всегда отдаёт ссылки на smotret-anime.org, независимо от использованного зеркала.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

# Поля для списка каталога. Без этого параметра API отдаёт ещё и episodes[],
# descriptions[] и links[] — две записи весят 38 КБ, весь каталог так не выкачать.
CATALOG_FIELDS = (
    "id,titles,allTitles,title,type,typeTitle,year,season,numberOfEpisodes,"
    "posterUrl,posterUrlSmall,url,genres,isAiring,isActive,isHentai,"
    # Из оценок берём только MAL: своей оценки у AniList в API нет (есть лишь id),
    # а worldArtScore решено не показывать.
    "myAnimeListId,anilistId,aniDbId,myAnimeListScore"
)

SERIES_DETAIL_FIELDS = CATALOG_FIELDS + ",descriptions,links,episodes"

TRANSLATION_FIELDS = (
    "id,seriesId,episodeId,type,typeKind,typeLang,qualityType,authorsList,"
    "authorsSummary,title,isActive,priority,addedDateTime,updatedDateTime,"
    # url — страница конкретного перевода конкретной серии на сайте.
    # Именно она нужна кнопке «смотреть», а не общая страница тайтла.
    "width,height,duration,episode,url"
)

MAX_LIMIT = 1000


class Anime365Error(RuntimeError):
    pass


class SubscriptionRequired(Anime365Error):
    """403 от embed: нет токена либо истекла премиум-подписка."""


@dataclass(frozen=True)
class EmbedInfo:
    translation_id: int
    download: list[dict[str, Any]]
    stream: list[dict[str, Any]]
    subtitles_url: str | None
    embed_url: str | None

    @property
    def heights(self) -> list[int]:
        """Реально доступные ступени скачивания, по убыванию.

        Из исходного разрешения не выводятся: встречается и 526 → [478], и 528 → [720, 360],
        а у 4K-источников потолок всё равно 1080.
        """
        return sorted({int(d["height"]) for d in self.download if d.get("height")}, reverse=True)

    def url_for_height(self, height: int) -> str | None:
        for item in self.download:
            if int(item.get("height", 0)) == height:
                return item.get("url")
        return None


class RateLimiter:
    """Минимальный интервал между запросами.

    Явного лимита у API не замечено (~60 запросов в минуту проходят спокойно),
    но упереться в бан на полном скане каталога не хочется.
    """

    def __init__(self, per_second: int) -> None:
        self._min_interval = 1.0 / per_second if per_second > 0 else 0.0
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class Anime365Client:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._limiter = RateLimiter(settings.get_int("request_rate_limit"))

    async def __aenter__(self) -> Anime365Client:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=False,
                headers={"User-Agent": settings.get_str("user_agent")},
                http2=True,
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # Транспорт
    # ------------------------------------------------------------------ #

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET с перебором зеркал.

        Зеркала берутся из серверного allowlist. Начинаем с выбранного base_url,
        затем пробуем остальные: при блокировке одного домена каталог продолжает работать.
        """
        assert self._client is not None, "используйте клиент через async with"

        params = dict(params or {})
        token = settings.get_str("access_token")
        if token and path.startswith("/api/translations/embed"):
            params["access_token"] = token

        origins = self._ordered_origins()
        last_error: Exception | None = None

        for origin in origins:
            await self._limiter.acquire()
            url = f"{origin}{path}"
            try:
                response = await self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                log.warning("Зеркало %s недоступно: %s", origin, exc)
                last_error = exc
                continue

            if response.status_code == 403:
                raise SubscriptionRequired(
                    "403 от Anime365: проверьте access_token и активность премиум-подписки"
                )
            if response.status_code == 404:
                return None
            if response.status_code >= 500:
                log.warning("Зеркало %s ответило %s", origin, response.status_code)
                last_error = Anime365Error(f"HTTP {response.status_code}")
                continue
            if response.status_code != 200:
                raise Anime365Error(f"HTTP {response.status_code} на {path}")

            payload = response.json()
            if "error" in payload:
                raise Anime365Error(f"API вернул ошибку: {payload['error']}")
            return payload.get("data")

        raise Anime365Error(f"Все зеркала недоступны для {path}: {last_error}")

    def _ordered_origins(self) -> list[str]:
        primary = settings.base_url()
        rest = [o for o in settings.allowed_origins() if o != primary]
        return [primary, *rest]

    @staticmethod
    def _with_token(url: str) -> str:
        """Добавляет access_token к ссылке скачивания.

        Без токена сайт отвечает HTML-страницей, а не файлом, причём со статусом
        200 — молчаливая подмена, которую легко не заметить.
        """
        token = settings.get_str("access_token")
        if not url or not token or "access_token=" in url:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}access_token={token}"

    @staticmethod
    def rewrite_host(url: str) -> str:
        """Переписывает хост ссылки на выбранное зеркало.

        embed всегда возвращает ссылки на smotret-anime.org, к какому бы зеркалу мы ни
        обращались. Проверено, что те же пути работают на любом зеркале, поэтому при
        блокировке .org без этой замены каталог работал бы, а загрузки молча падали.
        """
        if not url:
            return url
        parts = urlsplit(url)
        if not parts.netloc:
            # embed отдаёт subtitlesUrl относительным путём.
            return f"{settings.base_url()}{url}"

        known_hosts = {urlsplit(o).netloc for o in settings.allowed_origins()}
        if parts.netloc not in known_hosts:
            # CDN-ссылки не трогаем: они подписанные и живут на своих хостах.
            return url

        target = urlsplit(settings.base_url())
        return urlunsplit((target.scheme, target.netloc, parts.path, parts.query, parts.fragment))

    # ------------------------------------------------------------------ #
    # Аккаунт
    # ------------------------------------------------------------------ #

    async def me(self) -> dict[str, Any]:
        """Проверка токена и подписки.

        Единственный эндпоинт с пользовательскими данными: списков просмотренного,
        избранного и синка с Shikimori/AniList в API нет — все /api/me/* возвращают
        ровно этот же объект.
        """
        return await self._request("/api/me") or {}

    # ------------------------------------------------------------------ #
    # Каталог
    # ------------------------------------------------------------------ #

    async def iter_catalog(self, after_id: int = 0, fields: str = CATALOG_FIELDS):
        """Полное сканирование каталога через afterId.

        offset на больших списках работает очень медленно (предупреждение в документации API),
        поэтому только afterId. Весь каталог — 44 запроса и примерно 32 секунды.
        """
        while True:
            batch = await self._request(
                "/api/series",
                {"limit": MAX_LIMIT, "order": "id", "afterId": after_id, "fields": fields},
            )
            if not batch:
                return
            yield batch
            after_id = batch[-1]["id"]

    async def airing(self, fields: str = CATALOG_FIELDS) -> list[dict[str, Any]]:
        """Онгоинги — дешёвый «горячий» набор, около 306 тайтлов."""
        return await self._request(
            "/api/series", {"isAiring": 1, "limit": MAX_LIMIT, "fields": fields}
        ) or []

    async def series(self, series_id: int) -> dict[str, Any] | None:
        return await self._request(f"/api/series/{series_id}", {"fields": SERIES_DETAIL_FIELDS})

    async def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Онлайн-поиск. httpx сам кодирует кириллицу — без этого запрос вернёт пусто."""
        return await self._request(
            "/api/series", {"query": query, "limit": limit, "fields": CATALOG_FIELDS}
        ) or []

    # ------------------------------------------------------------------ #
    # Переводы
    # ------------------------------------------------------------------ #

    async def translations(self, series_id: int) -> list[dict[str, Any]]:
        """Все переводы тайтла.

        ВАЖНО: feed=id обязателен. Без него выдача не отсортирована по id, и постраничный
        обход через afterId возвращает пустую вторую страницу, хотя данные есть.
        У популярного тайтла бывает около 2000 переводов, то есть две-три страницы.
        """
        out: list[dict[str, Any]] = []
        after_id = 0
        while True:
            batch = await self._request(
                "/api/translations",
                {
                    "seriesId": series_id,
                    "feed": "id",
                    "afterId": after_id,
                    "limit": MAX_LIMIT,
                    "fields": TRANSLATION_FIELDS,
                },
            )
            if not batch:
                break
            out.extend(batch)
            after_id = batch[-1]["id"]
            if len(batch) < MAX_LIMIT:
                break
        return out

    async def embed(self, translation_id: int) -> EmbedInfo:
        """Ссылки на видео и субтитры. Требует токен с активной подпиской.

        Один запрос на перевод, поэтому вызывать только по необходимости:
        при раскрытии группы в UI и непосредственно перед скачиванием
        (подписанные CDN-ссылки живут недолго).
        """
        data = await self._request(f"/api/translations/embed/{translation_id}")
        if data is None:
            raise Anime365Error(f"Перевод {translation_id} не найден")

        subtitles_url = data.get("subtitlesUrl")
        return EmbedInfo(
            translation_id=translation_id,
            # ⚠️ Ссылкам на видео нужен access_token: без него сайт отдаёт HTML-страницу
            # со статусом 200, и она молча сохранялась бы вместо видеофайла.
            # Субтитрам токен не нужен — проверено.
            download=[
                {**d, "url": self._with_token(self.rewrite_host(d.get("url", "")))}
                for d in data.get("download") or []
            ],
            stream=data.get("stream") or [],
            # None у перевода с typeKind == "sub" означает вшитые субтитры.
            subtitles_url=self.rewrite_host(subtitles_url) if subtitles_url else None,
            embed_url=self.rewrite_host(data.get("embedUrl") or ""),
        )
