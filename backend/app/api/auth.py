"""Вход в UI: один общий пароль, сессия в куке.

Аккаунтов нет намеренно (см. план): для домашнего сервиса на 2-3 человек они
дают только лишнюю поверхность. Схема при этом готова — сессия уже несёт
`user_id`, а таблица `app_user` существует с первого дня.
"""

from __future__ import annotations

import asyncio
import logging
import secrets

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.core.security import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    login_limiter,
    login_name_limiter,
    password_required,
    sessions,
    verify_user,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    # Оба поля обязательны. Входа «по одному паролю без имени» больше нет:
    # учётная запись без имени не даёт ни разделения прав, ни понимания, кто
    # что сделал, а счётчик попыток по имени без него не работает вовсе.
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        # Secure ставим только на https: иначе браузер выбросит куку при
        # обращении по http://<ip>:8080, а это основной сценарий в домашней сети.
        secure=secure,
        path="/",
    )


@router.get("/status")
def auth_status(request: Request) -> dict[str, bool]:
    """Нужен ли вход и выполнен ли он. Единственный эндпоинт до логина."""
    required = password_required()
    session = sessions.get(request.cookies.get(SESSION_COOKIE))
    return {
        "password_required": required,
        "authenticated": not required or session is not None,
    }


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, object]:
    if not password_required():
        return {"ok": True, "password_required": False}

    key = _client_key(request)
    # Имя нормализуем: иначе «Admin» и «admin » считались бы разными целями и
    # обходили счётчик простой сменой регистра.
    name_key = payload.login.strip().casefold()

    # Два счётчика, и оба обязательны. Только по адресу — ботнет размазывает
    # попытки по сотне машин, и каждая остаётся в пределах лимита. Только по
    # имени — достаточно долбиться в чужой логин, чтобы запереть его владельца.
    wait = max(login_limiter.check(key), login_name_limiter.check(name_key))
    if wait > 0:
        response.status_code = 429
        return {"ok": False, "detail": f"Слишком много попыток, подождите {int(wait) + 1} с"}

    user_id = verify_user(payload.login, payload.password)
    if user_id is None:
        login_limiter.register_failure(key)
        login_name_limiter.register_failure(name_key)
        # Пауза бьёт по скорости перебора сильнее, чем лимит: даже в пределах
        # разрешённых попыток быстрее секунды не выйдет.
        await asyncio.sleep(1.0)
        # Ни имени, ни пароля в лог: в поле логина легко по ошибке ввести
        # пароль, а лог читают и хранят дольше, чем стоило бы хранить такую
        # ошибку. Адреса достаточно, чтобы заметить перебор.
        log.warning("Неудачная попытка входа с %s", key)
        response.status_code = 401
        # Формулировка одна на оба случая: «такого пользователя нет» подсказало
        # бы подбирающему, какие имена существуют.
        return {"ok": False, "detail": "Неверный логин или пароль"}

    login_limiter.reset(key)
    login_name_limiter.reset(name_key)
    token = sessions.create(user_id)
    _set_cookie(response, token, secure=request.url.scheme == "https")
    # Имя не пишем и здесь: журнал входов — отдельная задача, и делать его
    # побочным эффектом отладочной строки неправильно.
    log.info("Успешный вход с %s", key)
    return {"ok": True, "password_required": True}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    sessions.drop(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


def issue_session_after_password_change(
    response: Response, request: Request, user_id: int
) -> None:
    """После смены пароля старые сессии убиваются, а текущему клиенту выдаётся новая.

    Иначе тот, кто только что задал пароль, немедленно получит 401 на следующем
    же запросе — выглядит как поломка.

    ⚠️ `user_id` обязателен. Раньше здесь бралось значение по умолчанию, то есть
    пользователь `id=1`, и администратор, не являющийся корневым, после смены
    пароля продолжал работу **под чужой учётной записью**: права он при этом
    получал не свои, а задачи создавались от чужого имени.
    """
    sessions.drop_all()
    token = sessions.create(user_id)
    _set_cookie(response, token, secure=request.url.scheme == "https")


def random_password_hint() -> str:
    """Подсказка в UI, если пользователь хочет сгенерировать пароль."""
    return secrets.token_urlsafe(12)
