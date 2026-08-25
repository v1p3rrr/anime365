"""Прокси для исходящих запросов.

Ничего настраивать в коде не нужно: httpx создаётся с `trust_env=True` (это его
поведение по умолчанию) и сам читает стандартные переменные окружения. Значит
любой запрос к Anime365 — каталог, embed, скачивание, проксирование видео и
субтитров в плеер — пойдёт через прокси, если он задан.

Переменные стандартные, те же, что понимают curl и requests:

    HTTP_PROXY   — для http://
    HTTPS_PROXY  — для https://, а весь трафик к Anime365 идёт по https
    ALL_PROXY    — для всех схем сразу; сюда пишут socks5://…
    NO_PROXY     — исключения, через запятую

SOCKS5 требует пакета `socksio`, поэтому httpx подключён как `httpx[http2,socks]`.
Без него `ALL_PROXY=socks5://…` падает на первом же запросе с невнятным
сообщением про отсутствующий пакет.

Этот модуль нужен ровно для одного: сказать в лог при старте, через что пойдут
запросы. Неверно набранный адрес прокси иначе проявляется как «сайт недоступен»,
и искать причину приходится вслепую.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit, urlunsplit
from urllib.request import getproxies_environment, proxy_bypass_environment

log = logging.getLogger(__name__)

# Регистр значения не имеет: и `HTTPS_PROXY`, и `https_proxy` — рабочие
# написания, а окружение docker-compose обычно приходит в верхнем.
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


def _read(name: str) -> str | None:
    return os.environ.get(name) or os.environ.get(name.lower())


def mask_credentials(url: str) -> str:
    """Прячет логин и пароль в адресе прокси.

    Адрес попадает в лог, а `socks5://user:pass@host` — это учётные данные.
    Логировать их нельзя ровно по той же причине, по которой из логов вырезается
    access_token.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "(неразборчивый адрес)"

    if not parts.hostname or "@" not in parts.netloc:
        return url

    netloc = parts.hostname + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, f"***@{netloc}", parts.path, parts.query, parts.fragment))


def describe() -> str:
    """Строка о действующих прокси — для лога при старте."""
    active = [(name, _read(name)) for name in PROXY_VARS]
    active = [(name, value) for name, value in active if value]
    if not active:
        return "прокси не задан, запросы идут напрямую"

    return "; ".join(
        f"{name}={value if name == 'NO_PROXY' else mask_credentials(value)}"
        for name, value in active
    )


def route_for(url: str) -> str:
    """Через что пойдёт **этот** запрос: адрес прокси или «напрямую».

    `describe()` отвечает на вопрос «что вообще задано», а этот — «что применится
    к конкретному адресу». Разница существенная: `NO_PROXY` и разные схемы
    означают, что часть запросов идёт мимо прокси, и по общей строке из лога при
    старте этого не видно.

    Нужно для разбора отказов. На сервере пользователя таймауты к Anime365
    выглядели как сетевая поломка, хотя сеть была исправна, — и первым делом
    хотелось знать, действительно ли запрос шёл через прокси. Ответить было
    нечем: в логе стояло только «Зеркало недоступно».

    Берём `getproxies_environment()`, а не `getproxies()`: второй на Windows
    лезет ещё и в реестр, и ответ переставал бы совпадать с тем, что видит
    контейнер. httpx с `trust_env=True` читает те же переменные.

    ⚠️ Учётные данные из адреса вырезаются: строка идёт в лог.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    proxies = getproxies_environment()

    if not proxies:
        return "напрямую (прокси не задан)"
    if host and proxy_bypass_environment(host, proxies):
        return f"напрямую ({host} подходит под NO_PROXY)"

    chosen = proxies.get(parts.scheme) or proxies.get("all")
    if not chosen:
        return f"напрямую (для схемы {parts.scheme} прокси не задан)"
    return mask_credentials(chosen)


def log_status() -> None:
    log.info("Исходящие запросы: %s", describe())

    if _read("ALL_PROXY") and (_read("ALL_PROXY") or "").lower().startswith("socks"):
        try:
            import socksio  # noqa: F401
        except ImportError:
            # Ловим здесь, а не на первом запросе: иначе приложение поднимется
            # здоровым и начнёт падать на каждом обращении к сайту.
            log.error(
                "ALL_PROXY указывает на SOCKS, но пакет socksio не установлен — "
                "запросы к Anime365 будут падать. Пересоберите образ."
            )
