"""SSE-поток событий для интерфейса."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.events import bus, event_stream

log = logging.getLogger(__name__)

router = APIRouter(tags=["service"])


@router.get("/events")
async def events() -> StreamingResponse:
    """Живые события: прогресс загрузок, статусы задач, ход сканирования."""
    queue = await bus.subscribe()
    return StreamingResponse(
        event_stream(queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Отключает буферизацию в nginx: без этого события копятся и приходят пачкой.
            "X-Accel-Buffering": "no",
        },
    )
