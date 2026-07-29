"""Фоновые задачи.

Расписания разделены намеренно: полный рескан стоит 44 запроса и ~32 секунды,
гонять его каждый час бессмысленно. Инкремент ловит новые тайтлы за секунды,
а полный рескан нужен только чтобы заметить правки уже существующих записей —
поля updatedDateTime у тайтлов нет.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.services.catalog_sync import catalog_sync

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _incremental_job() -> None:
    try:
        await catalog_sync.incremental()
        await catalog_sync.airing()
    except Exception:
        # Расписание не должно умирать из-за одной неудачи: сеть могла моргнуть.
        log.exception("Инкрементальная синхронизация каталога не удалась")


async def _full_job() -> None:
    try:
        await catalog_sync.full()
    except Exception:
        log.exception("Полная синхронизация каталога не удалась")


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        _incremental_job,
        IntervalTrigger(hours=settings.get_int("catalog_refresh_hours")),
        id="catalog_incremental",
        # Если предыдущий запуск ещё идёт, новый не плодим.
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _full_job,
        IntervalTrigger(hours=settings.get_int("catalog_full_refresh_hours")),
        id="catalog_full",
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    _scheduler = scheduler
    log.info(
        "Планировщик запущен: инкремент каждые %d ч, полный рескан каждые %d ч",
        settings.get_int("catalog_refresh_hours"),
        settings.get_int("catalog_full_refresh_hours"),
    )
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Планировщик остановлен")
