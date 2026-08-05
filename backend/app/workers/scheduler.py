"""Фоновые задачи.

Расписания разделены намеренно: полный рескан стоит 44 запроса и ~32 секунды,
гонять его каждый час бессмысленно. Инкремент ловит новые тайтлы за секунды,
а полный рескан нужен только чтобы заметить правки уже существующих записей —
поля updatedDateTime у тайтлов нет.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.services.catalog_sync import catalog_sync
from app.services.fts import needs_rowid_rebuild, rebuild_all
from app.services.library_service import library_scanner
from app.services.library_watcher import WatcherState, library_watcher
from app.services.stream_probe import stream_probe
from app.services import autodownload
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
        return
    # Файлы приехали — самое время поискать к ним субтитры. Отдельной разовой
    # задачей, а не здесь же: этот обработчик вызывается из потока слежения за
    # файловой системой, а поиск ходит в сеть и может идти минутами.
    _kick_autodownload()


def _kick_autodownload() -> None:
    """Разовый запуск автоскачивания — сразу, но не в чужом потоке.

    Окно то же, что у расписания: только что появившиеся серии и попадают в
    него по определению, а гонять всю библиотеку на каждое изменение файлов
    незачем.
    """
    if _scheduler is None or settings.get_int("autodownload_interval_hours") <= 0:
        return
    try:
        _scheduler.add_job(
            _autodownload_job,
            id="autodownload_now",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    except Exception:
        log.exception("Не удалось запустить автоскачивание после изменений")


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


async def _autodownload_job() -> None:
    """Перепроверка серий без субтитров — по расписанию и с окном.

    Субтитры почти всегда выкладывают позже видео: серия приезжает от Сонарра
    вечером, а перевод появляется через день-два. Одной проверки в момент
    появления файла поэтому мало.

    Окно обязательно. Без него каждые несколько часов мы обходили бы всю
    библиотеку, спрашивая чужой сайт о переводах, которых нет и не будет.
    Старые серии не брошены: их разбирает кнопка «искать по всей библиотеке»,
    которая идёт без ограничения по сроку.

    Пока скан библиотеки идёт, не начинаем: разметка серий в этот момент
    меняется, и решение «субтитров нет» принималось бы по половине данных.
    """
    if library_scanner.running:
        return
    try:
        await autodownload.run_for_all(within_days=settings.get_int("autodownload_window_days"))
    except Exception:
        log.exception("Автоскачивание субтитров не удалось")


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


def _fts_rowid_job() -> None:
    """Одноразовая пересборка поискового индекса под новое правило.

    Индекс, собранный прежними версиями, раздавал `rowid` автоинкрементом, а
    теперь по нему берутся идентификаторы тайтлов — иначе поиск стоит 243 мс
    вместо 2 мс. Такую базу надо пересобрать один раз.

    Не в `bootstrap()`, а фоном: пересборка 29 тысяч тайтлов занимает около
    минуты, и задерживать на неё старт сервиса незачем. До её завершения поиск
    работает по-старому — записи со старым `rowid` просто не находятся, а
    каталог и всё остальное не затронуты.
    """
    try:
        if not needs_rowid_rebuild():
            return
        log.info("Поисковый индекс собран по старому правилу, пересобираю")
        count = rebuild_all()
        log.info("Поисковый индекс пересобран: %d тайтлов", count)
    except Exception:
        log.exception("Пересборка поискового индекса не удалась")


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone="UTC")

    # Через полминуты после старта: пусть сервис сначала поднимется и ответит
    # на healthcheck.
    scheduler.add_job(
        _fts_rowid_job,
        DateTrigger(run_date=datetime.now(UTC) + timedelta(seconds=30)),
        id="fts_rowid_rebuild",
    )

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

    # Ноль означает «по расписанию не проверять»: остаются проверка при
    # появлении новых файлов и кнопка «искать сейчас».
    if settings.get_int("autodownload_interval_hours") > 0:
        scheduler.add_job(
            _autodownload_job,
            IntervalTrigger(hours=settings.get_int("autodownload_interval_hours")),
            id="autodownload",
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
