"""Ошибки API с машинными кодами.

Интерфейс переведён на три языка, а сообщения об ошибках приходят с сервера.
Передавать в запросах выбранный язык мы не стали: язык — личная настройка и
живёт в браузере, сервер общий, и делать общее зависимым от личного значит
получить чужой язык в логах и в ответах на параллельные запросы.

Поэтому сервер отдаёт **код** случая, а текст собирает клиент по своему
словарю. Русское сообщение всё равно кладётся в ответ: по нему читаются логи,
`curl` и любой сторонний потребитель API, у которого словаря нет. Клиент берёт
его как запасной вариант, когда код ему незнаком — так добавление нового кода
на сервере не оставляет пользователя с пустым местом вместо объяснения.

`params` несут значения для подстановки (путь, имя, число). Держать их отдельно
обязательно: собранная строка на клиенте не разбирается, а переводы отличаются
порядком слов, и склеить их из кусков нельзя.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

if TYPE_CHECKING:
    from app.services.downloader import UnknownHostError

# --- Внешний API -------------------------------------------------------------
UPSTREAM_UNAVAILABLE = "upstream_unavailable"
SUBSCRIPTION_REQUIRED = "subscription_required"
TOKEN_MISSING = "token_missing"
RELATIONS_UNAVAILABLE = "relations_unavailable"

# --- Каталог -----------------------------------------------------------------
TOO_MANY_CARDS = "too_many_cards"
UNKNOWN_TRANSLATION_TYPE = "unknown_translation_type"
SYNC_RUNNING = "sync_running"

# --- Ограничение частоты -----------------------------------------------------
RATE_LIMITED = "rate_limited"

# --- Вход --------------------------------------------------------------------
#: Логин или пароль не подошли. Формулировка одна на оба случая намеренно:
#: «такого пользователя нет» подсказало бы подбирающему, какие имена есть.
BAD_CREDENTIALS = "bad_credentials"
#: Слишком много попыток входа. Отдельно от `RATE_LIMITED`: там речь про частоту
#: запросов к API и другие подстановки, а здесь — про ожидание в секундах.
LOGIN_THROTTLED = "login_throttled"

# --- Загрузки ----------------------------------------------------------------
ROOT_READ_ONLY = "root_read_only"
HARDSUB = "hardsub"
QUEUE_REJECTED = "queue_rejected"
TASK_NOT_FOUND = "task_not_found"
# Задача уже завершена — двигать её в очереди нечего.
TASK_NOT_ACTIVE = "task_not_active"

# --- Библиотека --------------------------------------------------------------
SCAN_RUNNING = "scan_running"
SCAN_REJECTED = "scan_rejected"
FOLDER_NOT_FOUND = "folder_not_found"
SERIES_NOT_FOUND = "series_not_found"
MAPPING_NOT_FOUND = "mapping_not_found"
TOO_MANY_NAMES = "too_many_names"
FILE_NOT_FOUND = "file_not_found"

# Коллекции в Jellyfin. Разведены намеренно: «выключено» чинится в настройках,
# «ничего не нашлось» — сканированием библиотеки, «недоступен» — сетью.
JELLYFIN_DISABLED = "jellyfin_disabled"
JELLYFIN_NOTHING_FOUND = "jellyfin_nothing_found"
JELLYFIN_UNAVAILABLE = "jellyfin_unavailable"
BIND_KIND_UNSUPPORTED = "bind_kind_unsupported"
FFPROBE_DISABLED = "ffprobe_disabled"
FFMPEG_MISSING = "ffmpeg_missing"
PROBE_FAILED = "probe_failed"

# --- Права доступа -----------------------------------------------------------
PERMISSION_DENIED = "permission_denied"
ADMIN_REQUIRED = "admin_required"
USER_EXISTS = "user_exists"
USER_NOT_FOUND = "user_not_found"
#: Пароль выдан временно: работать нельзя, пока не задан свой.
PASSWORD_CHANGE_REQUIRED = "password_change_required"
#: Запись, которую просили изменить, не найдена.
NOT_FOUND = "not_found"
CANNOT_REMOVE_LAST_ADMIN = "cannot_remove_last_admin"

# --- Настройки ---------------------------------------------------------------
SETTINGS_INVALID = "settings_invalid"
PATHS_OVERLAP = "paths_overlap"
SETTING_UNKNOWN = "setting_unknown"

# --- Просмотр ----------------------------------------------------------------
NO_PLAYABLE_LINKS = "no_playable_links"
UNKNOWN_HOST = "unknown_host"
UNSAFE_LINK = "unsafe_link"
STREAM_OPEN_FAILED = "stream_open_failed"
CDN_ERROR = "cdn_error"
STREAM_NOT_VIDEO = "stream_not_video"
SUBTITLES_HARDSUB = "subtitles_hardsub"
SUBTITLES_UNAVAILABLE = "subtitles_unavailable"
SUBTITLES_FETCH_FAILED = "subtitles_fetch_failed"
SUBTITLES_SERVER_ERROR = "subtitles_server_error"


class ApiError(HTTPException):
    """HTTP-ошибка, которую интерфейс умеет перевести.

    Тело ответа: `{"detail": {"code": ..., "message": ..., "params": {...}}}`.
    Форма совместима с обычным `HTTPException`: клиент, встретив строку вместо
    объекта, показывает её как есть.
    """

    def __init__(self, status_code: int, code: str, message: str, **params: Any) -> None:
        detail: dict[str, Any] = {"code": code, "message": message}
        if params:
            detail["params"] = params
        super().__init__(status_code=status_code, detail=detail)


def upstream_unavailable(exc: Exception) -> ApiError:
    """Ошибка обращения к Anime365, разложенная по существу.

    Разбор здесь, а не в каждом обработчике: вызовов больше десятка, и держать
    в них одинаковые ветки — верный способ забыть одну.

    Главное различие — между «сайт не отвечает» и «сайт отвечает отказом».
    Второе раньше показывалось как «зеркало заблокировано, смените в
    настройках», хотя менять надо было токен: Anime365 на неавторизованный
    запрос отвечает статусом 200 с телом `{"error": {"code": 403, ...}}`,
    и отказ терялся среди сетевых.
    """
    # Импорт локальный: anime365 тянет настройки и http-клиент, а errors нужен
    # всем и должен оставаться лёгким.
    from app.services.anime365 import SubscriptionRequired, TokenMissing

    if isinstance(exc, TokenMissing):
        return ApiError(403, TOKEN_MISSING, str(exc))

    if isinstance(exc, SubscriptionRequired):
        return ApiError(403, SUBSCRIPTION_REQUIRED, str(exc), reason=str(exc))

    return ApiError(502, UPSTREAM_UNAVAILABLE, f"Anime365 недоступен: {exc}", reason=str(exc))


def rejected_link(exc: UnknownHostError) -> ApiError:
    """Отказ по ссылке, которую не пропустил allowlist хостов (`UnknownHostError`).

    Один помощник на все эндпоинты: раньше плеер, «скачать себе» и субтитры
    отвечали на один и тот же случай тремя разными кодами, и подсказка про
    настройку была не везде. Тип — только для аннотации, как и в
    `upstream_unavailable`: errors нужен всем и должен оставаться лёгким.

    `unknown_host` — хост вне списка, лечится `A365_EXTRA_CDN_HOSTS`.
    `unsafe_link` — ссылка разобрана не так, как её поймёт получатель: логин в
    адресе, IP или посторонние знаки вместо имени хоста, пустой хост, схема не
    http(s). Настройкой это не лечится — полный список в `classify_target`.
    """
    code = UNKNOWN_HOST if exc.fixable else UNSAFE_LINK
    return ApiError(502, code, str(exc), host=exc.host)
