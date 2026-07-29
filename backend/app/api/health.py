"""Healthcheck для Docker и для быстрой проверки живости руками."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.core.config import settings
from app.core.db import check_connection

router = APIRouter(tags=["service"])


@router.get("/health")
def health(response: Response) -> dict:
    db_ok = check_connection()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if db_ok else "degraded",
        "version": "0.1.0",
        "db": db_ok,
        # Наличие токена показываем флагом: само значение наружу не отдаём никогда.
        "token_configured": bool(settings.get_str("access_token")),
        "password_set": bool(settings.get_str("ui_password")),
        "base_url": settings.base_url(),
    }
