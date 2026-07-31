"""Интеграция с Jellyfin: пинок на пересканирование и ссылка на страницу тайтла.

Две операции, и обе намеренно узкие. Модель данных Jellyfin мы не повторяем:
он остаётся источником, мы — только спрашиваем.

## Почему адресов два

Проверено на живом сервере: `/System/Info/Public` отдаёт
`LocalAddress: http://172.19.0.6:8096`, тогда как снаружи тот же сервер живёт на
`https://…`. За обратным прокси одна из двух ролей обязательно сломается, если
адрес один: либо контейнер не достучится до публичного имени, либо ссылка в
браузере приведёт на внутренний адрес, которого у пользователя нет.

Поэтому `jellyfin_url` — куда ходим сами, `jellyfin_public_url` — куда ведут
ссылки. Второй необязателен: без обратного прокси адрес один и тот же.

## Почему сопоставление по пути, а не по внешнему ID

Проверено по спецификации 10.11.8: у `GET /Items` **86 параметров и ни одного
про провайдеров**. Есть только булевы `hasImdbId`/`hasTmdbId`/`hasTvdbId` — «есть
ли вообще», а не «равен такому-то». Отфильтровать по AniList или Shikimori
запросом нельзя.

И не нужно. Папку тайтла мы знаем из своего скана, а Jellyfin отдаёт `Path` —
это точное соединение, без нечёткого матчинга и его порогов.

Таблица трансляции путей при этом не нужна: сравниваем **хвост** пути. Мы видим
`{корень}/YuruYuri - Happy Go Lily`, Jellyfin — `/media/anime/YuruYuri - Happy Go
Lily`; точки монтирования разные, structура под ними одна. Это избавляет
пользователя от настройки, которую иначе пришлось бы держать в согласии с двумя
`docker-compose` сразу.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings

# Версия, которой подписываемся перед Jellyfin. Держится рядом с той, что
# объявлена приложению в main.py.
APP_VERSION = "0.1.0"

log = logging.getLogger(__name__)

# Пауза перед запросом. Батч из полусотни серий завершается пачкой, и дёргать
# Jellyfin на каждую — значит запустить полсотни сканирований подряд.
DEBOUNCE_SECONDS = 30

# Сколько держим список элементов Jellyfin. Библиотека меняется редко, а обход
# у крупной коллекции не бесплатный: 5 минут дают странице тайтла отвечать из
# памяти, но не заставляют перезапускать приложение после добавления сериала.
INDEX_TTL_SECONDS = 300

# Типы, которые вообще могут быть «страницей тайтла». Season здесь потому, что
# у нас сезон — отдельный тайтл со своей папкой, и вести на общую страницу
# сериала было бы менее точно, чем на страницу конкретного сезона.
ITEM_TYPES = "Series,Season,Movie"

# Типы, у которых путь указывает на файл, а не на папку. Нужны для сверки
# «что Jellyfin не подобрал»: Series и Season — это каталоги, по ним видно
# только то, что папка замечена, а не то, что распознан каждый файл в ней.
FILE_ITEM_TYPES = "Episode,Movie"

REQUEST_TIMEOUT = 15.0


def usable_key(raw: str) -> str:
    """Ключ, пригодный для заголовка HTTP, или пустая строка.

    Заголовки обязаны быть ASCII. Ключ приходит из формы настроек, а туда его
    вставляют мышью — вместе с хвостовым переносом строки, пробелом, а иногда
    и кириллицей, если человек скопировал не то поле.

    Проверять это обязательно: `httpx` на таком значении бросает
    `UnicodeEncodeError`, и он **не** наследник `httpx.HTTPError`, то есть
    пролетает мимо обработчика сетевых сбоев и превращается в 500 на странице
    тайтла. Найдено проверкой на живом сервере, а не придумано.
    """
    key = (raw or "").strip()
    if not key:
        return ""
    try:
        key.encode("ascii")
    except UnicodeEncodeError:
        log.warning("Ключ Jellyfin содержит недопустимые символы и не будет отправлен")
        return ""
    return key


def _auth_headers(key: str) -> dict[str, str]:
    """Заголовки авторизации Jellyfin.

    Формат взят из отданного сервером бандла официального SDK, а не из памяти:

        'MediaBrowser Client="…", Device="…", DeviceId="…", Version="…", Token="…"'

    `X-Emby-Token` шлём вторым заголовком: он недокументирован, но принимается
    и старыми сборками, где схемы `MediaBrowser` ещё нет. Лишний заголовок
    ничего не стоит, а разбираться, почему на чьей-то версии 401, — стоит.

    Client/Device видны в Jellyfin в списке устройств, поэтому подписываемся
    внятно, а не анонимно.
    """
    token = quote(key, safe="")  # ключ уже проверен usable_key()
    parts = [
        'MediaBrowser Client="Anime365 Downloader"',
        'Device="Anime365 Downloader"',
        'DeviceId="anime365-downloader"',
        f'Version="{APP_VERSION}"',
        f'Token="{token}"',
    ]
    return {"Authorization": ", ".join(parts), "X-Emby-Token": key}


def _normalize(path: str) -> str:
    """Приводит путь к виду, пригодному для сравнения хвостов.

    Регистр гасим намеренно, хотя Jellyfin работает на Linux и файловая система
    там регистрозависимая: расхождение в регистре между нашей записью и его —
    гораздо более вероятная беда, чем две разные папки, различающиеся только
    регистром.
    """
    return path.replace("\\", "/").rstrip("/").casefold()


class JellyfinClient:
    def __init__(self) -> None:
        self._pending: asyncio.Task | None = None
        # (время загрузки, {хвост пути: {"id", "type"}})
        self._index: dict[str, dict[str, str]] = {}
        self._index_at: float = 0.0
        self._server_id: str | None = None
        self._user_id: str | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Адреса и состояние
    # ------------------------------------------------------------------ #

    @property
    def enabled(self) -> bool:
        """Интеграция готова к работе: включена И настроена."""
        return bool(self.turned_on and self.configured)

    @property
    def turned_on(self) -> bool:
        """Тумблер в настройках. Отдельно от `configured` намеренно.

        Пользователь, включивший тумблер и не заполнивший адрес, должен увидеть
        причину, а не пустое место: с его точки зрения интеграция включена.
        Схлопывать оба состояния в одно «выключено» — значит отвечать молчанием
        на осознанное действие.
        """
        return settings.get_bool("enable_jellyfin")

    @property
    def configured(self) -> bool:
        return bool(settings.get_str("jellyfin_url") and settings.get_str("jellyfin_api_key"))

    def missing_settings(self) -> list[str]:
        """Чего именно не хватает — чтобы подсказка называла это поимённо."""
        missing = []
        if not settings.get_str("jellyfin_url"):
            missing.append("url")
        if not settings.get_str("jellyfin_api_key"):
            missing.append("api_key")
        return missing

    @staticmethod
    def internal_base() -> str:
        return settings.get_str("jellyfin_url").rstrip("/")

    @staticmethod
    def public_base() -> str:
        """Адрес для ссылок. Без обратного прокси совпадает с внутренним."""
        return (settings.get_str("jellyfin_public_url") or settings.get_str("jellyfin_url")).rstrip("/")

    def forget(self) -> None:
        """Сбрасывает кэш. Зовётся при смене настроек и после раскладки файлов."""
        self._index = {}
        self._index_at = 0.0
        self._server_id = None
        self._user_id = None

    # ------------------------------------------------------------------ #
    # Запросы
    # ------------------------------------------------------------------ #

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        base = self.internal_base()
        key = usable_key(settings.get_str("jellyfin_api_key"))
        if not base:
            return None
        headers = _auth_headers(key) if key else {}
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(f"{base}{path}", params=params, headers=headers)
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            # Ловим шире сетевых сбоев намеренно: кривой адрес или заголовок
            # даёт не HTTPError, а страница тайтла не должна падать из-за
            # необязательной ссылки.
            log.warning("Jellyfin недоступен (%s): %s", path, exc or type(exc).__name__)
            return None
        if response.status_code >= 400:
            log.warning("Jellyfin ответил %s на %s", response.status_code, path)
            return None
        try:
            return response.json()
        except ValueError:
            log.warning("Jellyfin вернул не JSON на %s", path)
            return None

    async def server_id(self) -> str | None:
        """Идентификатор сервера — он нужен в ссылке.

        Берётся из `/System/Info/Public`, который **не требует ключа**: это
        единственный открытый эндпоинт Jellyfin, проверено.
        """
        if self._server_id:
            return self._server_id
        data = await self._get("/System/Info/Public")
        if isinstance(data, dict) and data.get("Id"):
            self._server_id = str(data["Id"])
        return self._server_id

    async def _ensure_index(self) -> dict[str, dict[str, str]]:
        """Список элементов Jellyfin с путями, разложенный по хвостам путей."""
        fresh = self._index and (time.monotonic() - self._index_at) < INDEX_TTL_SECONDS
        if fresh:
            return self._index

        # Под замком, иначе одновременно открытые страницы тайтлов устроят
        # несколько параллельных обходов библиотеки вместо одного.
        async with self._lock:
            if self._index and (time.monotonic() - self._index_at) < INDEX_TTL_SECONDS:
                return self._index

            data = await self._get(
                "/Items",
                {
                    "includeItemTypes": ITEM_TYPES,
                    "recursive": "true",
                    "fields": "Path",
                    "enableImages": "false",
                    "enableUserData": "false",
                    "enableTotalRecordCount": "false",
                },
            )
            items = (data or {}).get("Items") or []
            index: dict[str, dict[str, str]] = {}
            for item in items:
                path = item.get("Path")
                item_id = item.get("Id")
                if not path or not item_id:
                    # У Season путь бывает пустым, если сезон виртуальный
                    # (файлов нет, метаданные есть). Сопоставлять нечем.
                    continue
                index[_normalize(path)] = {"id": str(item_id), "type": str(item.get("Type") or "")}

            self._index = index
            self._index_at = time.monotonic()
            log.info("Jellyfin: получено элементов с путями %d", len(index))
            return index

    # ------------------------------------------------------------------ #
    # Сопоставление
    # ------------------------------------------------------------------ #

    async def find_item(self, rel_paths: list[str]) -> dict[str, str] | None:
        """Ищет элемент Jellyfin по относительным путям папок тайтла.

        `rel_paths` — пути папок относительно наших корней, в порядке
        предпочтения (первым идёт основная привязка).

        Совпадением считается элемент, чей путь **заканчивается** на наш
        относительный путь. Из нескольких берём самый длинный: если у тайтла
        привязана и `Show`, и `Show/Season 2`, точнее вторая.
        """
        if not self.enabled:
            return None

        index = await self._ensure_index()
        if not index:
            return None

        for rel in rel_paths:
            needle = _normalize(rel)
            if not needle:
                continue
            hits = [
                (full, meta)
                for full, meta in index.items()
                if full == needle or full.endswith("/" + needle)
            ]
            if not hits:
                continue
            # Самый длинный путь — самое конкретное совпадение. Короткое
            # «Show» совпало бы и с «Other Show», если бы мы не требовали
            # границу компонента, а требуем: сравнение идёт по «/» + хвост.
            full, meta = max(hits, key=lambda pair: len(pair[0]))
            return {"id": meta["id"], "type": meta["type"], "path": full}
        return None

    async def known_file_paths(self) -> set[str] | None:
        """Пути файлов, которые Jellyfin у себя распознал.

        `None` означает «спросить не удалось» — это принципиально не то же
        самое, что пустое множество: на пустом множестве сверка объявила бы
        нераспознанной всю библиотеку целиком.
        """
        data = await self._get(
            "/Items",
            {
                "includeItemTypes": FILE_ITEM_TYPES,
                "recursive": "true",
                "fields": "Path",
                "enableImages": "false",
                "enableUserData": "false",
                "enableTotalRecordCount": "false",
            },
        )
        if data is None:
            return None
        return {
            _normalize(item["Path"])
            for item in (data.get("Items") or [])
            if item.get("Path")
        }

    async def create_collection(self, name: str, item_ids: list[str]) -> dict[str, Any] | None:
        """Создаёт коллекцию из уже найденных элементов Jellyfin.

        Нужна как запасной путь. Обычно Jellyfin собирает коллекции сам, по
        данным TMDB — проверено на живой библиотеке: у коллекции Heaven's Feel
        стоит `Tmdb: 390636`, то есть она возникла автоматически. Но про часть
        тайтлов TMDB не знает вовсе, и для них связать части можно только руками
        или вот этой кнопкой.

        `ids` в API типом не ограничен, поэтому в одну коллекцию складываются и
        фильмы, и сериалы.
        """
        base = self.internal_base()
        key = usable_key(settings.get_str("jellyfin_api_key"))
        if not base or not key or not item_ids:
            return None

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.post(
                    f"{base}/Collections",
                    params={"name": name, "ids": ",".join(item_ids)},
                    headers=_auth_headers(key),
                )
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            log.warning("Не удалось создать коллекцию в Jellyfin: %s", exc)
            return None

        if response.status_code >= 400:
            log.warning("Jellyfin отказал в создании коллекции: %s", response.status_code)
            return None

        # Список элементов изменился — прежний кэш держать незачем.
        self.forget()
        try:
            return response.json()
        except ValueError:
            return {}

    async def collections(self) -> list[dict[str, str]] | None:
        """Существующие коллекции — чтобы было куда добавлять, а не только создавать."""
        data = await self._get(
            "/Items",
            {
                "includeItemTypes": "BoxSet",
                "recursive": "true",
                "enableImages": "false",
                "enableUserData": "false",
                "enableTotalRecordCount": "false",
            },
        )
        if data is None:
            return None
        items = [
            {"id": str(item["Id"]), "name": str(item.get("Name") or "")}
            for item in (data.get("Items") or [])
            if item.get("Id")
        ]
        items.sort(key=lambda item: item["name"].casefold())
        return items

    async def user_id(self) -> str | None:
        """Любой пользователь Jellyfin — он требуется части запросов.

        ⚠️ Без `userId` запрос состава коллекции возвращает ПУСТОЙ список, а не
        ошибку. Проверено: тот же запрос с `userId` отдаёт настоящие два
        элемента. Именно на этом список «в каких коллекциях состоит» врал.
        """
        if self._user_id:
            return self._user_id
        data = await self._get("/Users")
        if isinstance(data, list) and data:
            self._user_id = str(data[0].get("Id") or "") or None
        return self._user_id

    async def collection_members(self, collection_id: str) -> set[str]:
        """Идентификаторы того, что лежит в коллекции.

        Отдельным запросом на коллекцию: `Ancestors` у элемента коллекцию НЕ
        показывает — она не предок в дереве папок, это проверено. Коллекций
        обычно единицы, поэтому обход дешёвый.

        ⚠️ `recursive` здесь не нужен и вреден: он разворачивает сериал до
        отдельных серий, и в ответе вместо двух элементов приходит четырнадцать.
        Нам нужны прямые дети.
        """
        user = await self.user_id()
        if not user:
            return set()
        data = await self._get(
            "/Items",
            {
                "parentId": collection_id,
                "userId": user,
                "enableImages": "false",
                "enableUserData": "false",
                "enableTotalRecordCount": "false",
            },
        )
        if data is None:
            return set()
        return {str(item["Id"]) for item in (data.get("Items") or []) if item.get("Id")}

    async def add_to_collection(self, collection_id: str, item_ids: list[str]) -> bool:
        """Добавляет элементы в уже существующую коллекцию."""
        base = self.internal_base()
        key = usable_key(settings.get_str("jellyfin_api_key"))
        if not base or not key or not item_ids:
            return False
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.post(
                    f"{base}/Collections/{collection_id}/Items",
                    params={"ids": ",".join(item_ids)},
                    headers=_auth_headers(key),
                )
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            log.warning("Не удалось добавить в коллекцию Jellyfin: %s", exc)
            return False
        if response.status_code >= 400:
            log.warning("Jellyfin отказал в добавлении: %s", response.status_code)
            return False
        self.forget()
        return True

    async def item_url(self, item_id: str) -> str | None:
        """Ссылка на страницу элемента в веб-интерфейсе.

        Формат взят не из памяти, а из отданного сервером бандла веб-клиента:
        для `Series`/`Season`/`Episode` там строится
        `"#/details?id=" + id + "&serverId=" + serverId`.
        """
        base = self.public_base()
        if not base:
            return None
        server = await self.server_id()
        if not server:
            return None
        return f"{base}/web/#/details?id={item_id}&serverId={server}"

    async def resolve_title(self, rel_paths: list[str]) -> dict[str, str] | None:
        """Всё вместе: от путей папок до готовой ссылки."""
        item = await self.find_item(rel_paths)
        if not item:
            return None
        url = await self.item_url(item["id"])
        if not url:
            return None
        return {"url": url, "item_id": item["id"], "item_type": item["type"]}

    # ------------------------------------------------------------------ #
    # Пинок на пересканирование
    # ------------------------------------------------------------------ #

    def schedule(self) -> None:
        """Просит обновление, схлопывая частые вызовы в один."""
        if not self.enabled:
            return

        if self._pending is not None and not self._pending.done():
            # Уже запланировано — новая загрузка попадёт в то же обновление.
            return

        try:
            self._pending = asyncio.get_running_loop().create_task(self._run_later())
        except RuntimeError:
            # Вызвано вне событийного цикла (например, из синхронного кода
            # тестов) — тогда просто ничего не планируем.
            log.debug("Обновление Jellyfin не запланировано: нет активного цикла")

    async def _run_later(self) -> None:
        await asyncio.sleep(DEBOUNCE_SECONDS)
        await self.refresh()

    async def refresh(self) -> dict[str, object]:
        """Запускает сканирование библиотеки в Jellyfin.

        Возвращает результат, чтобы кнопкой «проверить» из настроек можно было
        убедиться, что адрес и ключ рабочие, а не гадать по логам.

        Важно: зовём обновление **всей** библиотеки, а не конкретной папки.
        Обновление папки требует пути в терминах Jellyfin, а полное обновление
        у него инкрементальное — он сам смотрит только изменившееся.
        """
        base = self.internal_base()
        raw_key = settings.get_str("jellyfin_api_key")
        key = usable_key(raw_key)
        if not base or not raw_key:
            return {"ok": False, "reason": "Не заданы адрес или ключ Jellyfin"}
        if not key:
            # Проверка из настроек должна назвать причину, а не молча упасть.
            return {"ok": False, "reason": "Ключ Jellyfin содержит недопустимые символы"}

        url = f"{base}/Library/Refresh"
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.post(url, headers=_auth_headers(key))
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            detail = str(exc) or type(exc).__name__
            log.warning("Jellyfin недоступен: %s", detail)
            return {"ok": False, "reason": f"Jellyfin недоступен: {detail}"}

        if response.status_code == 401:
            return {"ok": False, "reason": "Jellyfin не принял API-ключ"}
        if response.status_code >= 400:
            return {"ok": False, "reason": f"Jellyfin ответил {response.status_code}"}

        # Библиотека сейчас изменится — держать прежний список элементов незачем.
        self.forget()
        log.info("Jellyfin получил запрос на обновление библиотеки")
        return {"ok": True, "status": response.status_code}


jellyfin = JellyfinClient()
