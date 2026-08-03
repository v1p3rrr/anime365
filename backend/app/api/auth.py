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
from pydantic import BaseModel

from app.core.security import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    login_limiter,
    password_required,
    sessions,
    verify_password,
    verify_user,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str
    # Логин необязателен намеренно: вход по одному общему паролю никуда не
    # делся, и старые вкладки, которые его не шлют, продолжают работать.
    login: str | None = None


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
    wait = login_limiter.check(key)
    if wait > 0:
        response.status_code = 429
        return {"ok": False, "detail": f"Слишком много попыток, подождите {int(wait) + 1} с"}

    # С логином — вход под конкретным пользователем, и права берутся его.
    # Без логина — прежний общий пароль и пользователь по умолчанию.
    user_id: int | None = None
    if payload.login:
        user_id = verify_user(payload.login, payload.password)
        ok = user_id is not None
    else:
        ok = verify_password(payload.password)

    if not ok:
        login_limiter.register_failure(key)
        # Пауза бьёт по скорости перебора сильнее, чем лимит: даже в пределах
        # разрешённых попыток быстрее секунды не выйдет.
        await asyncio.sleep(1.0)
        # Имя в лог не пишем: в него легко по ошибке ввести пароль, а лог
        # читают и хранят дольше, чем стоило бы хранить такую ошибку.
        log.warning("Неудачная попытка входа с %s", key)
        response.status_code = 401
        # Формулировка одна на оба случая: «такого пользователя нет» подсказало
        # бы подбирающему, какие имена существуют.
        return {"ok": False, "detail": "Неверный логин или пароль"}

    login_limiter.reset(key)
    token = sessions.create(user_id) if user_id is not None else sessions.create()
    _set_cookie(response, token, secure=request.url.scheme == "https")
    log.info("Успешный вход с %s", key)
    return {"ok": True, "password_required": True}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    sessions.drop(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


def issue_session_after_password_change(response: Response, request: Request) -> None:
    """После смены пароля старые сессии убиваются, а текущему клиенту выдаётся новая.

    Иначе тот, кто только что задал пароль, немедленно получит 401 на следующем
    же запросе — выглядит как поломка.
    """
    sessions.drop_all()
    token = sessions.create()
    _set_cookie(response, token, secure=request.url.scheme == "https")


def random_password_hint() -> str:
    """Подсказка в UI, если пользователь хочет сгенерировать пароль."""
    return secrets.token_urlsafe(12)
