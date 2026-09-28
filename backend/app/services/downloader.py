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
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import AsyncIterator, Callable
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.services.anime365 import Anime365Client, Anime365Error, SubscriptionRequired

log = logging.getLogger(__name__)

CHUNK_SIZE = 1 << 20  # 1 МБ

# Домены CDN, на которые допустимо уходить по редиректу: сам домен и все его
# поддомены. Проверка нужна, чтобы подменённый ответ не увёл загрузку на
# посторонний сервер.
#
# ⚠️ Сайт меняет CDN без предупреждения, и тогда отказывает всё разом: очередь,
# плеер и «скачать себе». С 2026-09-27 вместо `*.b-cdn.net` приходит
# `bcdn.melon-soda.org/route/?m=…&r=…` — тот же одношаговый маршрут с подписью,
# сразу `200 video/mp4`. Чтобы следующий переезд не ждал релиза, новый домен
# дописывается в `A365_EXTRA_CDN_HOSTS` — он дополняет этот список, и записи
# обоих проходят через один и тот же `_in_domain` (D-214).
BUILTIN_CDN_DOMAINS = ("quantum-phantom-moon.ru", "b-cdn.net", "melon-soda.org")

# Имя хоста, которое браузер и httpx понимают одинаково. Всё прочее — повод
# отказать, а не угадывать: `https://evil.com\x.b-cdn.net/` для `urlsplit`
# оканчивается на `.b-cdn.net`, а браузер читает `\` как `/` и уходит на evil.com.
_PLAIN_HOSTNAME_RE = re.compile(r"[a-z0-9.-]+")
# IP вместо имени: CDN так не отвечает, а настройка IP не примет.
_IP_LIKE_RE = re.compile(r"[0-9.]+")

#: Ссылка ведёт на хост вне списка — возможно, новый CDN сайта.
REJECT_UNKNOWN = "unknown"
#: Ссылка разобрана не так, как её поймёт получатель: логин в адресе,
#: посторонние знаки в имени хоста, битый адрес. Настройкой это не лечится.
REJECT_SUSPICIOUS = "suspicious"


class DownloadCancelled(Exception):
    """Задачу отменили или поставили на паузу — это не ошибка."""


class UnknownHostError(Anime365Error):
    """Ссылка ведёт на хост вне allowlist или пришла в подозрительном виде.

    Имя хоста вынесено отдельно: его показывают человеку и пишут в лог, чтобы
    смену CDN на сайте можно было отличить от подмены и починить настройкой.
    Подсказка про настройку — в самом тексте: его без изменений показывают и
    очередь, и ответ «скачать себе», который браузер открывает как есть.
    """

    def __init__(self, url: str) -> None:
        self.reason = classify_target(url) or REJECT_UNKNOWN
        self.host = _hostname(url) or "?"
        if self.reason == REJECT_SUSPICIOUS:
            message = (
                f"Ссылка на {self.host} пришла в подозрительном виде (логин в адресе или "
                "посторонние знаки в имени хоста) и отклонена"
            )
        else:
            message = (
                f"Ссылка ведёт на неизвестный хост {self.host}. Если это новый CDN "
                "Anime365, его можно разрешить переменной A365_EXTRA_CDN_HOSTS на сервере"
            )
        super().__init__(message)

    @property
    def fixable(self) -> bool:
        """Поможет ли дописать хост в `A365_EXTRA_CDN_HOSTS`."""
        return self.reason == REJECT_UNKNOWN


@dataclass
class Progress:
    bytes_done: int
    bytes_total: int
    speed_bps: float


ProgressCallback = Callable[[Progress], None]


def _hostname(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _in_domain(host: str, domain: str) -> bool:
    """`melon-soda.org` пускает сам домен и поддомены, но не `evilmelon-soda.org`."""
    return host == domain or host.endswith("." + domain)


@lru_cache(maxsize=8)
def _allowed(
    mirrors: tuple[str, ...], extra: tuple[str, ...]
) -> tuple[frozenset[str], tuple[str, ...]]:
    """Хосты зеркал и домены CDN, собранные из настроек.

    Кэш по самим значениям настроек: они меняются раз в жизнь контейнера, а
    проверка идёт на каждом шаге каждого редиректа. Заодно лог о дублях
    пишется один раз, а не на каждую ссылку.
    """
    mirror_hosts = frozenset((_hostname(o) or "").lower() for o in mirrors)
    builtin = set(BUILTIN_CDN_DOMAINS)
    domains = list(BUILTIN_CDN_DOMAINS)
    for entry in extra:
        domain = entry.strip().strip(".").lower()
        covered = next((d for d in domains if _in_domain(domain, d)), None)
        if covered is None:
            domains.append(domain)
        elif covered in builtin:
            # Дубль ничего не ломает, но после обновления, где домен встроили,
            # строка в конфиге только сбивает с толку.
            log.info(
                "A365_EXTRA_CDN_HOSTS: %s уже покрыт встроенным %s — запись можно удалить",
                domain,
                covered,
            )
    return mirror_hosts, tuple(domains)


def classify_target(url: str) -> str | None:
    """None, если по ссылке можно идти; иначе причина отказа.

    Зеркала и CDN известны заранее; всё остальное отвергаем, чтобы подменённый
    ответ не заставил приложение качать с постороннего сервера.

    ⚠️ Хост берётся из `hostname`, а не нарезкой `netloc`. В `netloc` сидит ещё
    и логин с паролем: у `http://x.b-cdn.net:80@192.168.1.1/` до первого
    двоеточия стоит «свой» CDN, а соединение httpx открывает с `192.168.1.1`.
    Ссылки с логином в адресе CDN не выдаёт, поэтому они отвергаются целиком.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
        has_userinfo = parts.username is not None or parts.password is not None
    except ValueError:
        # Битый адрес вроде `http://[x` — `urlsplit` падает, а не отвечает.
        return REJECT_SUSPICIOUS
    if not host:
        return REJECT_UNKNOWN
    if has_userinfo or not _PLAIN_HOSTNAME_RE.fullmatch(host) or _IP_LIKE_RE.fullmatch(host):
        return REJECT_SUSPICIOUS

    mirror_hosts, domains = _allowed(
        settings.allowed_origins(), settings.get_list("extra_cdn_hosts")
    )
    if host in mirror_hosts or any(_in_domain(host, d) for d in domains):
        return None
    return REJECT_UNKNOWN


def is_allowed_target(url: str) -> bool:
    """Можно ли идти по ссылке — см. `classify_target`."""
    return classify_target(url) is None


#: Сколько редиректов проходим, прежде чем сдаться.
#:
#: Пять с запасом: у Anime365 цепочка «страница → CDN» длиной в один-два шага,
#: а длинная цепочка — это либо петля, либо попытка увести куда-то в сторону.
MAX_REDIRECT_HOPS = 5


def next_hop(current_url: str, location: str) -> str:
    """Абсолютный адрес следующего шага цепочки редиректов.

    ⚠️ `Location` по стандарту бывает **относительным** (`/route/?m=…`), и
    сравнивать его с allowlist напрямую бессмысленно: хоста в нём нет вовсе,
    проверка отвергла бы законный редирект. Склеиваем с текущим адресом.
    """
    from urllib.parse import urljoin

    return urljoin(current_url, location)


async def open_checked(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    *,
    stream: bool,
    method: str = "GET",
    timeout: float | None = None,
) -> httpx.Response:
    """Запрос с проверкой хоста на КАЖДОМ шаге редиректа.

    ⚠️ Ради этого функция и существует. Проверить только исходную ссылку мало:
    `follow_redirects=True` уводит клиент куда скажет ответ, а ответ приходит
    с чужой стороны. Подменённый или взломанный upstream отвечает `302` на
    `http://192.168.1.1/`, и приложение послушно сходит туда **из локальной
    сети сервера** — в типовой раскладке локальные диапазоны стоят в `NO_PROXY`,
    то есть запрос пойдёт напрямую, мимо прокси. Тело при этом уедет в браузер.

    Поэтому клиент обязан быть создан с `follow_redirects=False`, а цепочку
    проходим сами, сверяя каждый шаг с allowlist.

    ⚠️ Каждый адрес запрашивается ровно **один раз**. Подписи в ссылках CDN
    одноразовые: предварительный `HEAD` ради проверки убил бы ссылку, и
    настоящая загрузка получила бы ноль байт. Ответ редиректа тела не несёт,
    поэтому закрыть его и пойти дальше безопасно.
    """
    current = url
    for _ in range(MAX_REDIRECT_HOPS):
        if not is_allowed_target(current):
            raise UnknownHostError(current)

        build: dict[str, object] = {"headers": headers}
        if timeout is not None:
            build["timeout"] = timeout
        request = client.build_request(method, current, **build)  # type: ignore[arg-type]
        # ⚠️ `follow_redirects=False` задаётся ЗДЕСЬ, а не только при создании
        # клиента. Иначе защита держалась бы на том, что каждый вызывающий не
        # забыл выставить флаг: клиент со следованием прошёл бы цепочку сам,
        # вернул финальный ответ, и цикл ниже увидел бы `is_redirect == False`
        # — то есть отдал бы наружу непроверенный хост, ничего не заметив.
        response = await client.send(request, stream=stream, follow_redirects=False)

        if not response.is_redirect:
            return response

        location = response.headers.get("location")
        await response.aclose()
        if not location:
            raise Anime365Error(f"Редирект без Location при обращении к {_hostname(current)}")
        current = next_hop(current, location)

    raise Anime365Error(f"Слишком длинная цепочка редиректов от {_hostname(url)}")


@asynccontextmanager
async def checked_stream(
    client: httpx.AsyncClient, url: str, headers: dict[str, str]
) -> AsyncIterator[httpx.Response]:
    """`client.stream(...)`, но с проверкой хоста на каждом редиректе.

    Замена именно контекстным менеджером, а не отдельным вызовом: у загрузки
    тело большое и читается потоком, а закрывать ответ нужно при любом исходе.

    ⚠️ Ссылку **нельзя** разрешить заранее отдельным запросом и потом
    перезапросить: подписи CDN одноразовые, и повторное обращение вернёт ноль
    байт. Поэтому наружу отдаётся тот самый ответ, на котором цепочка
    закончилась.
    """
    response = await open_checked(client, url, headers, stream=True)
    try:
        yield response
    finally:
        await response.aclose()


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
            # ⚠️ Редиректы НЕ следуем автоматически: хост сверяется на каждом
            # шаге в `checked_stream`. Раньше здесь стояло `True`, и проверка
            # allowlist касалась только первой ссылки — то есть путь загрузок
            # уводился ответом чужого сервера куда угодно, включая внутреннюю
            # сеть, и делал это без человека за экраном, по расписанию
            # автоскачивания субтитров ([D-174](../../../claude/09-decisions.md)).
            follow_redirects=False,
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

        При включённом `try_direct_cdn` сначала пробуется прямой хост зоны
        BunnyCDN в обход прокси. Это эксперимент: если он не отвечает ДО того,
        как записан хоть один байт, молча уходим на обычную ссылку. Пробовать
        после первых байтов нельзя — подписи одноразовые, и повторный запрос
        по любой из ссылок вернёт ноль.
        """
        assert self._client is not None, "используйте загрузчик через async with"

        if expect == "video" and settings.get_bool("try_direct_cdn"):
            direct = direct_cdn_url(url)
            if direct:
                try:
                    started = time.monotonic()
                    size = await self._fetch_once(
                        direct, destination, on_progress, should_stop, expect
                    )
                    elapsed = max(time.monotonic() - started, 1e-6)
                    log.info(
                        "Прямой CDN сработал: %.1f МБ за %.1f с (%.1f МБ/с)",
                        size / (1 << 20),
                        elapsed,
                        size / (1 << 20) / elapsed,
                    )
                    return size
                except DownloadCancelled:
                    raise
                except Exception as exc:
                    part = destination.with_suffix(destination.suffix + ".part")
                    written = part.stat().st_size if part.exists() else 0
                    if written:
                        # Байты уже пошли — значит, дело не в недоступности хоста.
                        # Откатываться поздно: подпись израсходована.
                        raise
                    log.warning("Прямой CDN не сработал (%s), иду через прокси", exc)

        return await self._fetch_once(url, destination, on_progress, should_stop, expect)

    async def _fetch_once(
        self,
        url: str,
        destination: Path,
        on_progress: ProgressCallback | None = None,
        should_stop: Callable[[], bool] | None = None,
        expect: str = "video",
    ) -> int:
        """Одна попытка загрузки по конкретной ссылке."""
        assert self._client is not None, "используйте загрузчик через async with"

        if not is_allowed_target(url):
            raise UnknownHostError(url)

        part = destination.with_suffix(destination.suffix + ".part")
        part.parent.mkdir(parents=True, exist_ok=True)

        existing = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}

        started = time.monotonic()
        downloaded_now = 0

        async with checked_stream(self._client, url, headers) as response:
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

            # При сжатии content-length описывает СЖАТЫЙ ответ, а на диск пишется
            # распакованное — прогресс уходил за 100% («54 КБ из 13 КБ»).
            # В таком случае честнее показать неизвестный объём.
            compressed = bool(response.headers.get("content-encoding"))
            declared = int(response.headers.get("content-length", 0))
            total = 0 if compressed else existing + declared
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
    """Свободное место на томе, которому принадлежит путь.

    В контейнере это работает именно так, как нужно: `disk_usage` отдаёт
    статистику примонтированного тома, а не корневой ФС образа. Но у каждого
    тома она своя, поэтому одной проверки на всё приложение недостаточно —
    см. `check_free_space`.

    Если каталога ещё нет (первый запуск), поднимаемся к существующему
    родителю: иначе OSError выглядел бы как «диск переполнен».
    """
    import shutil

    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent

    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return 0


def parse_mountinfo(raw: str, target: PurePosixPath) -> str | None:
    """Какому монтированию из `/proc/self/mountinfo` принадлежит путь.

    Вынесено отдельной чистой функцией ради проверяемости: сам `_mount_id`
    зовёт `Path.resolve()`, а тот на Windows приписывает букву диска и делает
    посикс-пути из теста бессмысленными.
    """
    best_id: str | None = None
    best_len = -1
    for line in raw.splitlines():
        fields = line.split(" ")
        # mount_id parent_id major:minor root mount_point ...
        if len(fields) < 5:
            continue
        mount_id, point = fields[0], fields[4]
        # Пробелы и табы в путях ядро экранирует восьмеричными кодами.
        point = point.replace("\\040", " ").replace("\\011", "\t")
        candidate = PurePosixPath(point)
        if target == candidate or candidate in target.parents:
            # Побеждает самая длинная точка монтирования: вложенные монтирования
            # перекрывают внешние.
            if len(point) > best_len:
                best_id, best_len = mount_id, len(point)
    return best_id


def _mount_id(path: Path) -> str | None:
    """Идентификатор монтирования, в котором лежит путь.

    Возвращает None, если `mountinfo` недоступен — то есть везде, кроме Linux.
    """
    try:
        raw = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
        target = PurePosixPath(path.resolve())
    except (OSError, ValueError):
        return None
    return parse_mountinfo(raw, target)


def same_volume(a: Path, b: Path) -> bool:
    """Переедет ли файл из `a` в `b` переименованием, без копирования байт.

    Совпадения `st_dev` для этого **недостаточно**, и это не теория: проверено
    на двух bind-монтированиях одной хостовой папки — `st_dev` у них общий (69),
    а `os.rename` падает с `EXDEV`. Причина в ядре: при переименовании
    сравниваются не суперблоки, а `vfsmount`, то есть сами монтирования. Два
    отдельных `-v` в docker — это два монтирования, даже если за ними один диск
    и одна файловая система.

    Отсюда практический вывод для развёртывания: отдельный том под загрузки
    делает перемещение копированием. Чтобы оно было мгновенным, временная папка
    должна лежать **внутри того же монтирования**, что и библиотека.

    Здесь это важно ради учёта места: при копировании файл какое-то время
    существует в двух экземплярах, и на этой файловой системе нужен двойной
    запас. Раньше проверка по `st_dev` в такой конфигурации требовала место
    один раз — то есть была слишком мягкой ровно там, где нужна строгость.

    Вне Linux `mountinfo` нет, и мы откатываемся на `st_dev`: на одиночной
    машине разработчика это верно, а docker у нас всегда Linux.
    """
    left, right = _mount_id(a), _mount_id(b)
    if left is not None and right is not None:
        return left == right
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        # Не смогли определить — считаем разными: лишняя проверка безопаснее
        # пропущенной, а цена ошибки здесь только в чуть более строгом пороге.
        return False


def check_free_space(tmp_dir: Path, target_dir: Path, min_free_bytes: int) -> str | None:
    """Проверяет обе точки, куда файл попадёт: временную папку и библиотеку.

    Проверять только временную папку было ошибкой: файл скачивается в неё, а
    затем переезжает в библиотеку, и это, как правило, разные тома (в compose
    `./data` и медиатека монтируются отдельно). Нехватка места в библиотеке
    обнаруживалась бы уже после того, как сотни мегабайт скачаны.

    Возвращает текст ошибки или None, если места достаточно.
    """
    checks: list[tuple[str, Path]] = [("временной папке", tmp_dir)]
    if not same_volume(tmp_dir, target_dir):
        checks.append(("папке библиотеки", target_dir))

    for label, path in checks:
        free = free_space_bytes(path)
        if free < min_free_bytes:
            return (
                f"Недостаточно места в {label} ({path}): "
                f"свободно {free / (1 << 30):.1f} ГБ, требуется минимум "
                f"{min_free_bytes / (1 << 30):.0f} ГБ"
            )
    return None


# Прокси перед BunnyCDN и сама зона. Ссылка вида
#   https://ru-549959.quantum-phantom-moon.ru/s/q1561620/route/?m=…&r=…
# указывает на nginx, за которым стоит зона q1561620. Та же зона напрямую —
# https://moon-q1561620.b-cdn.net/route/?m=…&r=…  (префикс /s/{zone} убирается,
# с ним Bunny отвечает 404).
_PROXY_PATH_RE = re.compile(r"^/s/(?P<zone>[a-z0-9]+)(?P<rest>/.*)$", re.IGNORECASE)
_PROXY_HOST_SUFFIX = ".quantum-phantom-moon.ru"


def direct_cdn_url(url: str) -> str | None:
    """Прямая ссылка на зону BunnyCDN в обход прокси-хоста.

    ⚠️ Это **эксперимент**, а не оптимизация. На тестовом файле прямой хост был
    быстрее в 5.6 раза, но воспроизвести замер на реальном видео не удалось:
    подписи `m=`/`r=` одноразовые, повторный запрос отдаёт ноль байт. Плюс все
    замеры сделаны не из сети пользователя.

    Возвращает None, если ссылка не похожа на прокси-ссылку — тогда идём как
    обычно, без всяких переписываний.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if not parts.netloc.lower().endswith(_PROXY_HOST_SUFFIX):
        return None

    match = _PROXY_PATH_RE.match(parts.path)
    if not match:
        return None

    zone = match.group("zone")
    return urlunsplit(
        ("https", f"moon-{zone}.b-cdn.net", match.group("rest"), parts.query, "")
    )
