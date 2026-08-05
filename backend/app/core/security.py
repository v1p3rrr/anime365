"""Пароль на вход, сессии и ограничитель попыток.

Вход идёт по паре логин-пароль; пароль хранится argon2-хешем у самой учётной
записи. Сессия — непрозрачный токен в памяти процесса, привязанный к `user_id`;
после перезапуска контейнера нужно войти заново, и для домашнего сервиса это
скорее плюс, чем цена (см. D-24).
"""

from __future__ import annotations

import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from sqlalchemy import select
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings
from app.models import DEFAULT_USER_ID

log = logging.getLogger(__name__)

SESSION_COOKIE = "a365_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600

# Параметры по умолчанию argon2id. На слабом сервере проверка занимает десятки
# миллисекунд — для формы логина это незаметно, для перебора смертельно.
_hasher = PasswordHasher()

# Хеш заведомо недостижимого пароля — только чтобы тратить время на проверке
# несуществующего пользователя. Значение неважно, важна стоимость.
_DUMMY_HASH = _hasher.hash("нет такого пользователя")

# Полноценный argon2-хеш всегда начинается с `$argon2`. Всё остальное считаем
# паролем в открытом виде: так `A365_UI_PASSWORD=secret` в docker-compose
# работает без предварительного хеширования руками.
HASH_PREFIX = "$argon2"


#: Минимальная длина пароля, задаваемого через интерфейс.
#:
#: Десять — решение пользователя. Требований к составу (цифры, регистр,
#: спецсимволы) намеренно нет: они выталкивают людей к «Password1!», который
#: перебирается быстрее длинной строчки из обычных слов. Длина работает лучше
#: и запоминается, а argon2 и секундная пауза делают дорогой каждую попытку.
MIN_PASSWORD_LENGTH = 10


def password_problem(plain: str) -> str | None:
    """Что не так с паролем. `None` — годится.

    ⚠️ Проверяется только то, что задают **через интерфейс**. Пароль из
    окружения (`A365_UI_PASSWORD`) не проверяется намеренно: отказ на старте
    из-за короткого пароля означал бы, что сервис не поднимется и починить его
    будет нечем — интерфейс тоже не откроется. Там мы предупреждаем в лог.
    """
    if len(plain) < MIN_PASSWORD_LENGTH:
        return f"Пароль короче {MIN_PASSWORD_LENGTH} символов"
    if plain != plain.strip():
        # Пробел по краям почти всегда случайный (копирование из мессенджера),
        # а войти потом не выходит, и понять почему нельзя: поле-то скрытое.
        return "Пароль начинается или заканчивается пробелом"
    return None


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_user(login: str, plain: str) -> int | None:
    """Проверяет пару логин-пароль. Возвращает id пользователя или None.

    Пароль хранится argon2-хешем у самого пользователя. Пустой хеш означает,
    что под этим именем входить нельзя: такая запись существует только как
    владелец задач и прав.

    Сравнение идёт всегда, даже когда пользователя нет: иначе по времени ответа
    было бы видно, какие имена существуют, — а это половина подбора.
    """
    from app.core.db import session_scope
    from app.models import AppUser

    with session_scope() as session:
        user = session.execute(
            select(AppUser).where(AppUser.name == login.strip())
        ).scalar_one_or_none()
        stored = user.password_hash if user is not None else None
        user_id = user.id if user is not None else None

    if not stored:
        # Холостая проверка, чтобы время ответа не выдавало отсутствие записи.
        try:
            _hasher.verify(_DUMMY_HASH, plain)
        except (VerifyMismatchError, InvalidHashError):
            pass
        return None

    try:
        return user_id if _hasher.verify(stored, plain) else None
    except (VerifyMismatchError, InvalidHashError):
        return None


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

    def drop_for_user(self, user_id: int) -> int:
        """Выкидывает все сессии одной учётной записи. Возвращает сколько.

        Нужно при удалении пользователя и при смене его пароля. Без этого
        удалённый продолжал бы ходить по живой куке до истечения срока: запрос
        упирался бы в проверку и получал отказ, но это отказ по факту пропажи
        записи, а не отзыв доступа — разница видна, как только запись случайно
        заведут под тем же идентификатором.
        """
        with self._lock:
            doomed = [token for token, s in self._sessions.items() if s.user_id == user_id]
            for token in doomed:
                del self._sessions[token]
            return len(doomed)

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
    """Скользящее окно неудачных попыток.

    Нужен именно на логине: подбор пароля — самый дешёвый путь внутрь. Задержка
    на неудаче добивает остаток скорости перебора.

    Ключ передаёт вызывающий, и на логине их два: адрес и имя пользователя.
    Только по адресу мало — ботнет распределяет попытки по сотне адресов, и
    каждый остаётся в пределах лимита, пока пароль одной учётки перебирается
    как ни в чём не бывало. Только по имени тоже мало: тогда достаточно
    долбиться в чужой логин, чтобы запереть его владельца.

    Задержка растёт: после исчерпания лимита окно удваивается за каждый
    следующий промах, до потолка. Разовая ошибка человека стоит ему минут, а
    перебор упирается в часы.
    """

    def __init__(
        self, limit: int = 10, window: float = 300.0, max_window: float = 3600.0
    ) -> None:
        self.limit = limit
        self.window = window
        self.max_window = max_window
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}

    def _window_for(self, failures: int) -> float:
        """Окно с удвоением после каждого промаха сверх лимита."""
        extra = max(0, failures - self.limit)
        return min(self.window * (2**extra), self.max_window)

    def check(self, key: str) -> float:
        """Возвращает 0, если попытка разрешена, иначе — сколько секунд ждать."""
        now = time.time()
        with self._lock:
            recorded = self._attempts.get(key, [])
            window = self._window_for(len(recorded))
            hits = [t for t in recorded if now - t < window]
            self._attempts[key] = hits
            if len(hits) < self.limit:
                return 0.0
            return window - (now - hits[0])

    def register_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._attempts.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


#: По адресу — против перебора с одной машины.
login_limiter = AttemptLimiter()

#: По имени пользователя — против перебора, размазанного по многим адресам.
#: Лимит выше, чем по адресу: у семьи один внешний адрес на всех, и общий
#: счётчик по нему исчерпывался бы чужими опечатками.
login_name_limiter = AttemptLimiter(limit=20, window=300.0)


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


#: Из чего собирается временный пароль.
#:
#: Похожие символы выброшены намеренно: `0/O`, `1/l/I`. Пароль диктуют вслух
#: или переписывают с экрана, и «единица или буква эль» — самая частая причина
#: неудачного входа. Спецсимволы взяты те, что не ломают копирование через
#: терминал и не требуют экранирования: без кавычек, слэшей и обратных кавычек.
_TEMP_LOWER = "abcdefghijkmnopqrstuvwxyz"
_TEMP_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_TEMP_DIGITS = "23456789"
_TEMP_SPECIAL = "!@#$%^&*?-_=+"

#: Длина временного пароля и состав — требование пользователя.
TEMP_PASSWORD_LENGTH = 15
TEMP_PASSWORD_SPECIALS = (3, 5)
TEMP_PASSWORD_DIGITS = (2, 4)


def generate_temporary_password() -> str:
    """Пароль, который сервер выдаёт на один раз.

    Придумывать его руками администратору не даём: удобный пароль здесь
    означает слабый, а живёт эта строка до первого входа владельца.

    Состав задан пользователем: 15 символов, 3–5 спецсимволов, 2–4 цифры,
    заглавные и строчные латинские буквы. Каждая обязательная часть кладётся
    явно, а не «авось выпадет»: случайная выборка из общего алфавита регулярно
    даёт пароль вовсе без цифр, и требование оказалось бы выполнено не всегда.

    Перемешивание — `SystemRandom`, а не `random`: обычный генератор
    предсказуем по нескольким выданным значениям, и пароли перестали бы быть
    паролями.
    """
    rng = secrets.SystemRandom()
    specials = rng.randint(*TEMP_PASSWORD_SPECIALS)
    digits = rng.randint(*TEMP_PASSWORD_DIGITS)

    chars = [rng.choice(_TEMP_SPECIAL) for _ in range(specials)]
    chars += [rng.choice(_TEMP_DIGITS) for _ in range(digits)]
    # Обе регистровые группы обязаны присутствовать, поэтому по одной кладём
    # сразу, а остаток добираем произвольными буквами.
    chars.append(rng.choice(_TEMP_LOWER))
    chars.append(rng.choice(_TEMP_UPPER))
    letters = _TEMP_LOWER + _TEMP_UPPER
    chars += [rng.choice(letters) for _ in range(TEMP_PASSWORD_LENGTH - len(chars))]

    rng.shuffle(chars)
    return "".join(chars)
