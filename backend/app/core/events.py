"""Шина событий для живого обновления интерфейса.

Однонаправленный поток «сервер → браузер», поэтому SSE, а не WebSocket: меньше
кода, работает через любой прокси и переподключается сам.

Подписчиков немного (два-три открытых вкладки), поэтому каждому даётся своя
очередь с ограниченным размером: медленный клиент не должен тормозить воркеры.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator

log = logging.getLogger(__name__)

# Если клиент не успевает читать, старые события выбрасываем: прогресс загрузки
# ценен свежестью, а не полнотой.
QUEUE_MAXSIZE = 100


@dataclass
class Event:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        body = json.dumps(
            {"kind": self.kind, "at": datetime.utcnow().isoformat(), **self.payload},
            ensure_ascii=False,
        )
        return f"event: {self.kind}\ndata: {body}\n\n"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    def publish(self, kind: str, **payload: Any) -> None:
        """Рассылает событие. Вызывается из синхронного кода воркеров."""
        event = Event(kind, payload)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Выбрасываем самое старое и кладём новое: свежий прогресс важнее.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()


async def event_stream(queue: asyncio.Queue[Event]) -> AsyncIterator[str]:
    """Поток SSE. Периодически шлёт комментарий, чтобы прокси не рвал соединение."""
    try:
        yield ": connected\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20.0)
                yield event.to_sse()
            except asyncio.TimeoutError:
                # Комментарий SSE: браузер его игнорирует, но соединение живёт.
                yield ": keepalive\n\n"
    except asyncio.CancelledError:
        raise
    finally:
        await bus.unsubscribe(queue)
