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
