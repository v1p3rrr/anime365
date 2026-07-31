"""Зависимости роутеров: ограничитель частоты для дорогих эндпоинтов.

Дорогие — это те, что ходят в сеть к Anime365 или обходят файловую систему.
Ограничитель здесь не про защиту от злоумышленника (сервис домашний), а про
залипшую вкладку и случайный цикл в коде: они способны за минуту сделать сотни
запросов к API и довести аккаунт до бана.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request

from app.core import errors
from app.core.errors import ApiError

from app.core.security import rate_limiter


def rate_limit(name: str, limit: int, window: float) -> Callable[[Request], None]:
    """Не более `limit` обращений за `window` секунд с одного адреса."""

    def dependency(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if not rate_limiter.allow(f"{name}:{client}", limit, window):
            raise ApiError(
                429,
                errors.RATE_LIMITED,
                f"Слишком часто. Разрешено {limit} запросов за {int(window)} с — подождите немного.",
                limit=limit,
                window=int(window),
            )

    return dependency
