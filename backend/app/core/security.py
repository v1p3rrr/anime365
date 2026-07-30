"""Пароль на вход, сессии и ограничитель попыток.

Аккаунтов пока нет — один общий пароль, как решено в плане. Но сессии сразу
привязаны к `user_id`, поэтому включение аккаунтов не потребует переделки:
достаточно перестать подставлять DEFAULT_USER_ID.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings
from app.models import DEFAULT_USER_ID

log = logging.getLogger(__name__)

SESSION_COOKIE = "a365_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600

# Параметры по умолчанию argon2id. На слабом сервере проверка занимает десятки
# миллисекунд — для формы логина это незаметно, для перебора смертельно.
_hasher = PasswordHasher()

# Полноценный argon2-хеш всегда начинается с `$argon2`. Всё остальное считаем
# паролем в открытом виде: так `A365_UI_PASSWORD=secret` в docker-compose
# работает без предварительного хеширования руками.
HASH_PREFIX = "$argon2"


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str) -> bool:
    """Сверяет введённый пароль с настройкой. Пустая настройка = вход открыт."""
    stored = settings.get_str("ui_password")
    if not stored:
        return True

    if stored.startswith(HASH_PREFIX):
        try:
            return _hasher.verify(stored, plain)
        except (VerifyMismatchError, InvalidHashError):
            return False

    # Сравнение постоянного времени: обычное `==` выходит на первом отличии
    # и выдаёт длину общего префикса замером времени. Сравниваем байты:
    # для строк compare_digest требует чистый ASCII, а пароль вполне может
    # оказаться кириллическим.
    return hmac.compare_digest(stored.encode("utf-8"), plain.encode("utf-8"))


def password_required() -> bool:
    return bool(settings.get_str("ui_password"))


# --------------------------------------------------------------------------- #
# Сессии
# --------------------------------------------------------------------------- #


@dataclass
class Session:
    token: str
    user_id: int
    expires_at: float


class SessionStore:
    """Сессии в памяти.

    Держать их в БД смысла нет: перезапуск контейнера — редкое событие, а
    повторный ввод пароля после него скорее плюс. Зато нет ещё одной таблицы
    с секретами, которую нужно чистить.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}

    def create(self, user_id: int = DEFAULT_USER_ID) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._prune_locked()
            self._sessions[token] = Session(token, user_id, time.time() + SESSION_TTL_SECONDS)
        return token

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if session.expires_at < time.time():
                del self._sessions[token]
                return None
            return session

    def drop(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def drop_all(self) -> None:
        """Вызывается при смене пароля: старые сессии больше не действительны."""
        with self._lock:
            self._sessions.clear()

    def _prune_locked(self) -> None:
        now = time.time()
        expired = [t for t, s in self._sessions.items() if s.expires_at < now]
        for token in expired:
            del self._sessions[token]


sessions = SessionStore()


# --------------------------------------------------------------------------- #
# Ограничитель попыток входа
# --------------------------------------------------------------------------- #


class AttemptLimiter:
    """Скользящее окно неудачных попыток по IP.

    Нужен именно на логине: пароль один на всех, поэтому его подбор — самый
    дешёвый путь внутрь. Задержка на неудаче добивает остаток скорости перебора.
    """

    def __init__(self, limit: int = 10, window: float = 300.0) -> None:
        self.limit = limit
        self.window = window
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}

    def check(self, key: str) -> float:
        """Возвращает 0, если попытка разрешена, иначе — сколько секунд ждать."""
        now = time.time()
        with self._lock:
            hits = [t for t in self._attempts.get(key, []) if now - t < self.window]
            self._attempts[key] = hits
            if len(hits) < self.limit:
                return 0.0
            return self.window - (now - hits[0])

    def register_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._attempts.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


login_limiter = AttemptLimiter()


class RateLimiter:
    """Ограничитель для дорогих эндпоинтов (поиск, embed, батч-постановка).

    Считает запросы в окне на пару (ключ, IP). Смысл не в защите от DDoS —
    сервис домашний, — а в том, чтобы одна залипшая вкладка не устроила
    сотню запросов к Anime365 в секунду и не привела к бану аккаунта.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < window]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


rate_limiter = RateLimiter()
