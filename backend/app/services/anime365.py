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

from app.core import proxy
from app.core.config import settings

log = logging.getLogger(__name__)

#: Запросы к API, идущие прямо сейчас. Нужен только для диагностики отказов:
#: «таймаут, когда параллельно шёл один запрос» и «таймаут, когда их было сорок» —
#: это две разные истории, а по прежнему логу они выглядели одинаково.
#:
#: Метки-пустышки, а не адреса: два одинаковых адреса одновременно — обычное
#: дело (две вкладки с одним тайтлом), и множество строк схлопнуло бы их в одну,
#: занизив счёт.
_inflight: set[object] = set()

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


class RateLimited(Anime365Error):
    """Сайт попросил сбавить темп. Внутреннее: наружу не выходит."""

    def __init__(self, retry_after: float | None) -> None:
        super().__init__("429 от Anime365")
        self.retry_after = retry_after


# Явного лимита у API не замечено, но если он появится — молча падать нельзя:
# фоновый скан обходит сотни тайтлов, и одна отповедь не повод бросать проход.
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BACKOFF = 5.0


class TokenMissing(Anime365Error):
    """Токен вообще не задан.

    Отдельно от `SubscriptionRequired`: там токен есть, но не принят — надо
    проверять подписку. Здесь задавать нечего, и совет ровно один.
    """


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

    @property
    def stream_heights(self) -> list[int]:
        """Ступени, доступные для ПРОСМОТРА, по убыванию.

        Отдельно от `heights` намеренно: `download` и `stream` — разные списки
        в ответе embed, и совпадение их наборов ниоткуда не следует. Предлагать
        в плеере ступень из списка скачивания значило бы обещать то, чего может
        не оказаться; выбор бы молча сползал на другое качество.
        """
        return sorted({int(d["height"]) for d in self.stream if d.get("height")}, reverse=True)

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


def _payload_error_code(error: Any) -> int | None:
    """Числовой код из ошибки в теле ответа, если он там есть."""
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, int) else None


def _error_from_payload(error: Any, path: str) -> Anime365Error:
    """Разбирает ошибку, пришедшую В ТЕЛЕ ответа.

    Anime365 отвечает на неавторизованный запрос статусом 200 и телом вида
    `{"error": {"code": 403, "message": "You should login first."}}`. Из-за
    этого отказ по токену раньше попадал в общую ветку и показывался как
    «зеркало недоступно, попробуйте сменить» — совет, который уводит в сторону
    от настоящей причины.
    """
    code = None
    message = ""
    if isinstance(error, dict):
        code = error.get("code")
        message = str(error.get("message") or "")

    if code == 403 or "login" in message.lower():
        # Различаем два случая: токена нет вовсе — тогда советовать проверять
        # подписку бессмысленно; токен есть, но отвергнут — тогда дело в нём
        # самом или в сроке подписки.
        if not settings.get_str("access_token"):
            return TokenMissing(
                "Токен доступа не задан — Anime365 отвечает отказом на любой запрос. "
                "Задайте access_token в настройках"
            )
        return SubscriptionRequired(
            "Anime365 не принял токен. Проверьте его в настройках и убедитесь, "
            "что премиум-подписка активна"
        )

    return Anime365Error(f"API вернул ошибку: {error}")


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
        """GET с перебором зеркал и отступом при 429.

        Отступ экспоненциальный и уважает заголовок `Retry-After`, если он есть.
        Повторяем ограниченное число раз: если сайт просит подождать всерьёз,
        упрямство сделает только хуже.
        """
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                return await self._request_once(path, params)
            except RateLimited as exc:
                if attempt == RATE_LIMIT_RETRIES:
                    raise Anime365Error(
                        f"Anime365 ограничил частоту запросов на {path} и не отпустил "
                        f"за {RATE_LIMIT_RETRIES} попыток"
                    ) from exc
                delay = exc.retry_after or RATE_LIMIT_BACKOFF * (2**attempt)
                log.warning("429 от Anime365 на %s, ждём %.1f с", path, delay)
                await asyncio.sleep(delay)
        # Недостижимо: цикл либо возвращает, либо бросает.
        raise Anime365Error(f"Не удалось выполнить запрос {path}")

    async def _request_once(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET с перебором зеркал.

        Зеркала берутся из серверного allowlist. Начинаем с выбранного base_url,
        затем пробуем остальные: при блокировке одного домена каталог продолжает работать.
        """
        assert self._client is not None, "используйте клиент через async with"

        params = dict(params or {})
        token = params.pop("_token_override", None) or settings.get_str("access_token")
        if token:
            # Токен идёт со ВСЕМИ запросами к API, а не только с embed.
            # Раньше он подставлялся лишь для /api/translations/embed, из-за чего
            # /api/me отвечал isLogined: false, и проверка токена в настройках
            # всегда говорила «токен не опознан» даже на рабочем токене.
            params["access_token"] = token

        origins = self._ordered_origins()
        last_error: Exception | None = None

        for attempt, origin in enumerate(origins, start=1):
            queued = time.monotonic()
            await self._limiter.acquire()
            started = time.monotonic()
            url = f"{origin}{path}"
            marker = object()
            _inflight.add(marker)
            try:
                response = await self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                # Текст httpx часто пустой (например, у ConnectTimeout), поэтому
                # к сообщению обязательно добавляем класс исключения — без него
                # в UI приходило бы просто «Все зеркала недоступны: ».
                detail = str(exc) or exc.__class__.__name__
                # ⚠️ Подробности обязательны, и вот почему. На сервере
                # пользователя эти таймауты шли сутками, а строка в логе
                # говорила только «Зеркало недоступно» — по ней нельзя было
                # отличить оборванную сеть от запроса, ушедшего мимо прокси, от
                # ожидания в очереди лимитера. Полдня ушло на проверку сети,
                # которая оказалась исправной. Здесь всё, чего тогда не хватало.
                log.warning(
                    "Зеркало %s недоступно: %s (%s). Запрос %s, зеркало %s из %s, "
                    "ждал ответа %.0f мс, до этого в очереди лимитера %.0f мс, "
                    "маршрут: %s, одновременных запросов: %s",
                    origin,
                    detail,
                    type(exc).__name__,
                    path,
                    attempt,
                    len(origins),
                    (time.monotonic() - started) * 1000,
                    (started - queued) * 1000,
                    proxy.route_for(url),
                    len(_inflight),
                )
                last_error = Anime365Error(f"{type(exc).__name__}: {detail}")
                continue
            finally:
                _inflight.discard(marker)

            # Успех тоже стоит уметь посмотреть — но не в обычном логе, а по
            # требованию: `A365_LOG_LEVELS=app.services.anime365=DEBUG` (D-158).
            # Без этого «медленно» и «не отвечает» невозможно отличить.
            log.debug(
                "%s → %s за %.0f мс (маршрут: %s)",
                path,
                response.status_code,
                (time.monotonic() - started) * 1000,
                proxy.route_for(url),
            )

            if response.status_code == 403:
                raise SubscriptionRequired(
                    "403 от Anime365: проверьте access_token и активность премиум-подписки"
                )
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                # Отступ делает вызывающий: перебирать зеркала бессмысленно,
                # лимит почти наверняка общий на аккаунт, а не на домен.
                retry_after = response.headers.get("retry-after")
                try:
                    delay = float(retry_after) if retry_after else None
                except ValueError:
                    delay = None
                raise RateLimited(delay)
            if response.status_code >= 500:
                log.warning("Зеркало %s ответило %s", origin, response.status_code)
                last_error = Anime365Error(f"HTTP {response.status_code}")
                continue
            if response.status_code != 200:
                raise Anime365Error(f"HTTP {response.status_code} на {path}")

            payload = response.json()
            if "error" in payload:
                # Anime365 сообщает об ошибке ДВУМЯ способами: статусом ответа и
                # телом при статусе 200. Второй путь легко просмотреть, и тогда
                # любая беда выглядит как «сайт недоступен». Приводим оба к
                # одному виду, чтобы дальше по коду разницы не было.
                body_code = _payload_error_code(payload["error"])

                if body_code == 404:
                    # То же, что и HTTP 404: записи нет. Вызывающий разберётся
                    # сам — у него это штатный случай, а не сбой.
                    return None

                if body_code == 429:
                    # Пусть отработает общий отступ, а не падение: лимит —
                    # состояние временное.
                    raise RateLimited(None)

                if body_code is not None and body_code >= 500:
                    # Беда на стороне сайта, а не в запросе. Пробуем зеркало:
                    # они независимы, и соседнее может ответить.
                    log.warning("Зеркало %s вернуло в теле ошибку %s", origin, body_code)
                    last_error = Anime365Error(f"Ошибка {body_code} в ответе")
                    continue

                raise _error_from_payload(payload["error"], path)
            return payload.get("data")

        tried = ", ".join(o.replace("https://", "") for o in origins)
        raise Anime365Error(
            f"Ни одно зеркало не ответило на {path}. Пробовали: {tried}. "
            f"Последняя ошибка — {last_error}"
        )

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

    async def me(self, token: str | None = None) -> dict[str, Any]:
        """Проверка токена и подписки.

        `token` позволяет проверить ещё не сохранённое значение из формы: иначе
        пользователю пришлось бы сначала сохранить непроверенный токен.

        Единственный эндпоинт с пользовательскими данными: списков просмотренного,
        избранного и синка с Shikimori/AniList в API нет — все /api/me/* возвращают
        ровно этот же объект.
        """
        params = {"_token_override": token} if token else None
        return await self._request("/api/me", params) or {}

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

            # ⚠️ Курсор обязан сдвинуться вперёд. Пагинация здесь держится на
            # том, что сайт вернёт записи по возрастанию id (`order=id`), — а у
            # соседнего эндпоинта эта же схема уже подводила: без `feed=id`
            # выдача переводов не отсортирована (`claude/02-api-findings.md`).
            # Не сдвинувшийся курсор означал бы вечный цикл: тот же запрос, тот
            # же ответ, и так до конца света — с записью тысячи строк на каждом
            # обороте и с флагом «синхронизация идёт», который больше никогда не
            # снимется.
            #
            # Проверка ничего не стоит на рабочем пути: пока ответ отсортирован,
            # `batch[-1]["id"]` строго больше курсора.
            next_id = batch[-1]["id"]
            if next_id <= after_id:
                log.warning(
                    "Каталог: обход прекращён, курсор не сдвинулся "
                    "(afterId=%s, последний id в ответе=%s)",
                    after_id,
                    next_id,
                )
                return
            after_id = next_id

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

            # ⚠️ Та же защита, что у обхода каталога: курсор обязан сдвинуться.
            # Короткая страница обрывает цикл и так, но **полная** страница с
            # несдвинувшимся курсором давала бы вечный цикл. Здесь это опаснее
            # всего: именно у переводов пагинация уже ломалась без `feed=id`.
            next_id = batch[-1]["id"]
            if next_id <= after_id:
                log.warning(
                    "Переводы тайтла %s: обход прекращён, курсор не сдвинулся "
                    "(afterId=%s, последний id в ответе=%s)",
                    series_id,
                    after_id,
                    next_id,
                )
                break
            after_id = next_id
            if len(batch) < MAX_LIMIT:
                break
        return out

    async def translation(self, translation_id: int) -> dict[str, Any] | None:
        """Один перевод по его идентификатору.

        Нужен ровно для одного: у файла в имени стоит `t{id}`, а какому тайтлу
        этот перевод принадлежит — неизвестно. Ответ содержит `seriesId`, и
        дальше тайтл догружается целиком обычным путём.

        Отдельный метод, а не `translations(series_id)`, потому что вопрос
        обратный: не «какие переводы у тайтла», а «чей это перевод».
        """
        return await self._request(
            f"/api/translations/{translation_id}",
            {"fields": "id,seriesId,episodeId,typeKind,typeLang"},
        )

    async def download_sizes(self, info: EmbedInfo) -> dict[str, int]:
        """Вес файла по каждой ступени, до начала загрузки.

        Размер не приходит ни в списке переводов, ни в embed — его знает только
        CDN. Узнаём запросом HEAD: он отдаёт `Content-Length` и не качает тело.

        Часть узлов на HEAD не отвечает, поэтому при неудаче пробуем `Range`
        на один байт — ответ 206 несёт полный размер в `Content-Range`.

        Ошибки не пробрасываем: вес — справочная величина, и ради неё нельзя
        ломать выпадашку выбора качества. Не узнали — просто не показали.
        """
        assert self._client is not None, "используйте клиент через async with"

        sizes: dict[str, int] = {}
        for item in info.download:
            height = item.get("height")
            url = item.get("url")
            if not height or not url:
                continue
            size = await self._probe_size(url)
            if size is not None:
                sizes[str(int(height))] = size
        return sizes

    async def _probe_size(self, url: str) -> int | None:
        """Вес файла до скачивания: сперва `HEAD`, при отказе — `Range`.

        ⚠️ Адрес приходит из ответа `embed` и **не проходит** через
        `rewrite_host`: тот намеренно пропускает чужие хосты, потому что
        CDN-ссылки подписанные и живут на своих доменах. Значит проверять
        его надо здесь, иначе проба уходит по любому адресу, который вернул
        чужой API, — включая внутреннюю сеть ([D-174](../../../claude/09-decisions.md)).

        Импорт локальный: `downloader` тянет этот модуль сверху, и обычный
        импорт замкнул бы кольцо.
        """
        from app.services.downloader import open_checked

        for method, headers, pick in (
            ("HEAD", {}, self._length_from_headers),
            ("GET", {"Range": "bytes=0-0"}, self._length_from_range),
        ):
            try:
                response = await open_checked(
                    self._client, url, headers, stream=False, method=method, timeout=20
                )
            except (Anime365Error, httpx.HTTPError):
                # Неизвестный хост или сеть: вес не выясним, но и не роняем
                # выдачу — сводка качества просто останется без цифры.
                continue
            try:
                size = pick(response)
            except ValueError:
                size = None
            if size is not None:
                return size

        return None

    @staticmethod
    def _length_from_headers(response: httpx.Response) -> int | None:
        length = response.headers.get("content-length")
        if response.status_code < 400 and length:
            return int(length)
        return None

    @staticmethod
    def _length_from_range(response: httpx.Response) -> int | None:
        # `Content-Range: bytes 0-0/83960238` — полный размер после дроби.
        content_range = response.headers.get("content-range", "")
        if "/" in content_range:
            total = content_range.rsplit("/", 1)[1].strip()
            if total.isdigit():
                return int(total)
        return None

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
