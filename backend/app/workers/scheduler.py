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
from app.services.stream_probe import stream_probe
from app.services.translation_scan import scan_interval_hours, scan_rpm, scan_scope, translation_scan

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


async def _translation_job() -> None:
    """Сбор сведений о доступных типах перевода.

    Не пересекается с синхронизацией каталога намеренно: обе ходят на сайт, и
    одновременный запуск удвоил бы частоту запросов ровно тогда, когда мы её и
    стараемся держать низкой.
    """
    if catalog_sync.running:
        log.info("Синхронизация каталога идёт, сбор сведений о переводах отложен")
        return
    try:
        await translation_scan.run()
    except Exception:
        log.exception("Сбор сведений о переводах не удался")


async def _stream_probe_job() -> None:
    """Определение дорожек — следом за сканом библиотеки.

    Отдельной джобой, а не внутри скана: скан синхронный и быстрый (секунды),
    а этот обход растянут паузами и может идти минутами. Смешивать их значило
    бы держать признак «идёт сканирование» всё это время.

    Тумблер проверяется внутри сервиса, поэтому при выключенном ffprobe джоба
    просто ничего не делает и не стоит ничего.
    """
    if library_scanner.running:
        return
    try:
        await stream_probe.run()
    except Exception:
        log.exception("Определение дорожек не удалось")


def _library_job() -> None:
    """Сверочное сканирование библиотеки.

    При работающем слежении это страховка на случай пропущенных событий и
    изменений во время простоя контейнера, поэтому интервал большой. Если
    слежение недоступно (сетевая ФС, упор в лимит inotify) — основной механизм.

    Идёт ПОЛНЫМ проходом, в отличие от реакции на события: инкрементальный режим
    сравнивает mtime папки и не видит замену файла на месте под тем же именем.
    Это и есть та расхождение, ради которого сверочный проход существует.
    """
    if library_scanner.running:
        return
    try:
        library_scanner.scan_all(full=True)
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
    # Идёт по тому же расписанию, что и сверочный скан, но отдельной задачей:
    # к этому моменту скан уже расставил файлы, и остаётся добрать дорожки.
    scheduler.add_job(
        _stream_probe_job,
        IntervalTrigger(hours=settings.get_int("library_scan_hours")),
        id="stream_probe",
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

    # Ноль часов отключает джобу целиком: у кого библиотека не растёт, лишние
    # запросы к сайту не нужны вовсе.
    if scan_interval_hours() > 0:
        scheduler.add_job(
            _translation_job,
            IntervalTrigger(hours=scan_interval_hours()),
            id="translation_scan",
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
    if scan_interval_hours() > 0:
        log.info(
            "Сбор сведений о переводах каждые %d ч, охват %s, темп %d запросов в минуту",
            scan_interval_hours(),
            scan_scope(),
            scan_rpm(),
        )
    else:
        log.info("Сбор сведений о переводах выключен (A365_TRANSLATION_SCAN_HOURS=0)")
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    library_watcher.stop()
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Планировщик остановлен")
