"""Healthcheck для Docker и для быстрой проверки живости руками.

⚠️ Единственный эндпоинт, открытый без сессии и при этом что-то рассказывающий
о самой установке. Поэтому подробности отданы только вошедшим: версия на
неавторизованном адресе позволяет прицельно подобрать эксплойт под конкретный
релиз, а `password_set` подсказывает, стоит ли вообще пытаться. Проверке
здоровья контейнера ничего из этого не нужно — ей нужен код 200.

Открытым остаётся ровно то, что и есть проверка живости: жив ли процесс и
отвечает ли база.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.core.config import settings
from app.core.db import check_connection
from app.core.security import SESSION_COOKIE, password_required, sessions

router = APIRouter(tags=["service"])


def _may_see_details(request: Request) -> bool:
    """Показывать ли подробности установки.

    Пароль не задан — прятать не от кого: интерфейс и так открыт любому, кто
    дотянулся до порта, и молчание здесь было бы показной секретностью.
    """
    if not password_required():
        return True
    return sessions.get(request.cookies.get(SESSION_COOKIE)) is not None


@router.get("/health")
def health(request: Request, response: Response) -> dict:
    db_ok = check_connection()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    payload: dict = {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
    }

    if not _may_see_details(request):
        return payload

    payload |= {
        "version": "0.1.0",
        # Наличие токена показываем флагом: само значение наружу не отдаём никогда.
        "token_configured": bool(settings.get_str("access_token")),
        "password_set": bool(settings.get_str("ui_password")),
        "base_url": settings.base_url(),
    }
    return payload
