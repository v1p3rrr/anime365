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
from app.services.library_service import library_scanner
from app.services.library_watcher import WatcherState, library_watcher

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


def _on_library_changed(folders: set) -> None:
    """Реакция на изменения в файловой системе.

    Пересканируем целиком, а не только затронутые папки: полный проход по
    библиотеке занимает секунды, тогда как частичное обновление потребовало бы
    отдельной ветки логики со своими краевыми случаями (переименование папки,
    перенос файла между сезонами). Дебаунс гарантирует, что запуск будет один
    на серию изменений, а не на каждый файл.
    """
    if library_scanner.running:
        log.debug("Сканирование уже идёт, изменения будут учтены им")
        return
    try:
        library_scanner.scan_all()
    except Exception:
        log.exception("Пересканирование после изменений в файловой системе не удалось")


def _library_job() -> None:
    """Сверочное сканирование библиотеки.

    При работающем слежении это страховка на случай пропущенных событий и
    изменений во время простоя контейнера, поэтому интервал большой. Если
    слежение недоступно (сетевая ФС, упор в лимит inotify) — основной механизм.
    """
    if library_scanner.running:
        return
    try:
        library_scanner.scan_all()
    except Exception:
        log.exception("Сканирование библиотеки не удалось")


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
    scheduler.add_job(
        _library_job,
        IntervalTrigger(hours=settings.get_int("library_scan_hours")),
        id="library_scan",
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    _scheduler = scheduler

    # Слежение за ФС само решит, доступно ли оно; при отказе останется расписание.
    library_watcher.set_callback(_on_library_changed)
    status = library_watcher.start()
    if status.state == WatcherState.ACTIVE:
        log.info("Библиотека отслеживается в реальном времени")
    else:
        log.warning("Библиотека обновляется по расписанию: %s", status.reason)

    log.info(
        "Планировщик запущен: инкремент каждые %d ч, полный рескан каждые %d ч, "
        "сверочное сканирование библиотеки каждые %d ч",
        settings.get_int("catalog_refresh_hours"),
        settings.get_int("catalog_full_refresh_hours"),
        settings.get_int("library_scan_hours"),
    )
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    library_watcher.stop()
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Планировщик остановлен")
