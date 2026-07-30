"""Очередь загрузок: постановка задач, воркеры, постобработка.

Конвейер:

    queued → downloading → downloaded → postprocessing → done
                 ↓ ошибка                    ↓
              failed (бэкофф) → queued    failed

Постобработка вынесена отдельным шагом, потому что перемещение между дисками
может занимать заметное время, и блокировать им скачивание не нужно.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.db import session_scope
from app.core.events import bus
from app.core.paths import UnsafePathError, resolve_within
from app.models.catalog import Series, Translation
from app.models.downloads import ACTIVE_KEY, ACTIVE_STATUSES, DownloadTask, TaskStatus
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot, TitleMapping
from app.services.anime365 import Anime365Error, SubscriptionRequired
from app.services.downloader import (
    DownloadCancelled,
    Downloader,
    check_free_space,
    pick_download_url,
)
from app.services.naming import (
    NamingContext,
    build_target_name,
    folder_name_for_title,
    pick_neighbour,
    unique_path,
)

log = logging.getLogger(__name__)

SUBTITLE_EXTENSION = ".ass"
VIDEO_EXTENSION = ".mp4"


class QueueError(RuntimeError):
    pass


class ReadOnlyLibrary(QueueError):
    """Целевая папка смонтирована только для чтения."""


class NoTargetFolder(QueueError):
    """Тайтл не привязан ни к одной папке — складывать некуда."""


def _primary_folder(series_id: int) -> tuple[LibraryFolder, LibraryRoot] | None:
    """Основная папка тайтла — куда складывать скачанное."""
    from app.services.mapping_info import ensure_primary_mapping

    ensure_primary_mapping(series_id)

    with session_scope() as session:
        row = session.execute(
            select(TitleMapping, LibraryFolder, LibraryRoot)
            .join(LibraryFolder, TitleMapping.folder_id == LibraryFolder.id)
            .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
            .where(TitleMapping.series_id == series_id, TitleMapping.is_primary.is_(True))
        ).first()
        if row is None:
            return None
        _, folder, root = row
        session.expunge(folder)
        session.expunge(root)
        return folder, root


def ensure_target_folder(series_id: int) -> tuple[LibraryFolder, LibraryRoot] | None:
    """Папка для загрузки: существующая привязка либо новая папка.

    Тайтла может не быть в библиотеке вовсе — это штатный случай, когда качают
    что-то новое. Тогда создаём папку по названию тайтла в подходящем корне
    (фильмы отдельно от сериалов) и сразу привязываем: дальше всё работает как
    с любой другой папкой, включая маппинг серий и подсчёт наличия.
    """
    existing = _primary_folder(series_id)
    if existing is not None:
        return existing

    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            return None
        titles = dict(series.titles or {})
        # Решение пользователя перевешивает тип из каталога: у тайтла с
        # type=movie и несколькими эпизодами это связка фильмов, которую
        # Radarr фильмом не считает, и класть её надо к сериалам.
        is_movie = (
            series.treat_as_movie if series.treat_as_movie is not None else series.type == "movie"
        )
        year = series.year

    folder_name = folder_name_for_title(titles, year=year, is_movie=is_movie)
    if not folder_name:
        log.error("У тайтла %s нет пригодного названия для папки", series_id)
        return None

    root_kind = "movie" if is_movie else "series"
    with session_scope() as session:
        root = session.execute(
            select(LibraryRoot).where(
                LibraryRoot.kind == root_kind, LibraryRoot.is_available.is_(True)
            )
        ).scalars().first()
        if root is None:
            log.error("Нет доступного корня библиотеки для типа %s", root_kind)
            return None
        if not root.is_writable:
            log.error("Корень %s только для чтения — папку не создать", root.path)
            return None
        root_id, root_path = root.id, root.path

    target = Path(root_path) / folder_name
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("Не удалось создать папку %s: %s", target, exc)
        return None

    with session_scope() as session:
        folder = session.execute(
            select(LibraryFolder).where(
                LibraryFolder.root_id == root_id, LibraryFolder.rel_path == folder_name
            )
        ).scalar_one_or_none()
        if folder is None:
            folder = LibraryFolder(
                root_id=root_id,
                rel_path=folder_name,
                name=folder_name,
                season_number=None,
                video_count=0,
            )
            session.add(folder)
            session.flush()

        exists = session.execute(
            select(TitleMapping).where(
                TitleMapping.series_id == series_id, TitleMapping.folder_id == folder.id
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                TitleMapping(
                    series_id=series_id,
                    folder_id=folder.id,
                    source="created",
                    confidence=100.0,
                    matched_on="папка создана при загрузке",
                    is_manual=False,
                    is_primary=True,
                )
            )

    log.info("Создана папка для тайтла %s: %s", series_id, target)
    return _primary_folder(series_id)


def _existing_video_for_episode(folder_id: int, episode: float) -> str | None:
    """Имя уже лежащего видеофайла этой серии.

    Нужно для субтитров: их имя обязано совпасть с basename видео.
    """
    with session_scope() as session:
        row = session.execute(
            select(LibraryFile.filename)
            .where(
                LibraryFile.folder_id == folder_id,
                LibraryFile.kind == "video",
                LibraryFile.episode == episode,
            )
            .limit(1)
        ).scalar_one_or_none()
    return row


def _folder_filenames(folder_id: int) -> list[str]:
    with session_scope() as session:
        return [
            row[0]
            for row in session.execute(
                select(LibraryFile.filename).where(LibraryFile.folder_id == folder_id)
            ).all()
        ]


def already_downloaded(translation_id: int) -> bool:
    """Этот перевод уже лежит локально.

    Файлы, скачанные штатным загрузчиком сайта, содержат id перевода в имени —
    сканер их распознаёт, поэтому повторную загрузку можно не делать.
    """
    with session_scope() as session:
        return (
            session.execute(
                select(LibraryFile.id)
                .where(LibraryFile.anime365_translation_id == translation_id)
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )


def enqueue(
    translation_id: int,
    kind: str,
    requested_height: int | None = None,
    user_id: int = 1,
) -> dict[str, Any]:
    """Ставит задачу в очередь. Идемпотентно по переводу, типу и качеству."""
    with session_scope() as session:
        translation = session.get(Translation, translation_id)
        if translation is None:
            raise QueueError(f"Перевод {translation_id} не найден в кэше")

        series = session.get(Series, translation.series_id)
        series_title = (series.titles or {}).get("romaji") or (series.title if series else "")

        # Уже стоит в очереди или качается — второй раз не ставим.
        existing = session.execute(
            select(DownloadTask).where(
                DownloadTask.translation_id == translation_id,
                DownloadTask.kind == kind,
                DownloadTask.active_key == ACTIVE_KEY,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"id": existing.id, "status": existing.status, "created": False}

        task = DownloadTask(
            translation_id=translation_id,
            series_id=translation.series_id,
            episode=None,
            kind=kind,
            requested_height=requested_height or settings.get_int("preferred_height"),
            series_title=series_title,
            authors=translation.authors_summary or "",
            lang=translation.type_lang,
            type_kind=translation.type_kind,
            created_by_user_id=user_id,
            active_key=ACTIVE_KEY,
        )

        # Номер серии берём из кэша эпизодов.
        from app.models.catalog import Episode

        if translation.episode_id:
            episode = session.get(Episode, translation.episode_id)
            if episode is not None:
                task.episode = episode.episode_num

        session.add(task)
        session.flush()
        return {"id": task.id, "status": task.status, "created": True}


def _finish(task_id: int, status: str, error: str | None = None) -> None:
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            return
        task.status = status
        task.last_error = error
        task.finished_at = datetime.utcnow()
        # Снимаем задачу с уникального ключа, чтобы её можно было поставить заново.
        task.active_key = task.id

    bus.publish("download.status", task_id=task_id, status=str(status), error=error)


def _fail_with_backoff(task_id: int, error: str) -> None:
    """Помечает падение и назначает время повтора."""
    max_attempts = settings.get_int("max_attempts")
    base = settings.get_int("retry_backoff_base")

    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            return
        task.attempts += 1
        task.last_error = error[:2000]

        if task.attempts >= max_attempts:
            task.status = TaskStatus.FAILED
            task.finished_at = datetime.utcnow()
            task.active_key = task.id
            log.error("Задача %s исчерпала попытки: %s", task_id, error)
            return

        delay = base * (2 ** (task.attempts - 1))
        task.status = TaskStatus.QUEUED
        task.retry_after = datetime.utcnow() + timedelta(seconds=delay)
        log.warning("Задача %s упала (%s), повтор через %d с", task_id, error, delay)

    bus.publish("download.status", task_id=task_id, status="retrying", error=error)


class DownloadQueue:
    def __init__(self) -> None:
        self._workers: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._paused = False
        self._cancelled: set[int] = set()

    # ------------------------------------------------------------------ #
    # Управление
    # ------------------------------------------------------------------ #

    @property
    def paused(self) -> bool:
        return self._paused

    def pause_all(self) -> None:
        self._paused = True

    def resume_all(self) -> None:
        self._paused = False

    def cancel(self, task_id: int) -> None:
        self._cancelled.add(task_id)

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        count = settings.get_int("max_concurrent_downloads")
        # 0 означает «без ограничения», но бесконечных воркеров не бывает:
        # берём разумный потолок, иначе сотня параллельных загрузок положит сеть.
        workers = 8 if count == 0 else count

        self._recover_interrupted()

        self._stop.clear()
        for index in range(workers):
            self._workers.append(asyncio.create_task(self._worker(index)))

        # Постобработка идёт своим воркером и в одном экземпляре: параллельное
        # перемещение файлов на один диск только отнимает у него головки, а
        # выигрыша не даёт.
        self._workers.append(asyncio.create_task(self._postprocess_worker()))
        log.info("Очередь загрузок запущена: воркеров %d + постобработка", workers)

    async def stop(self) -> None:
        self._stop.set()
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()

    # ------------------------------------------------------------------ #
    # Воркер
    # ------------------------------------------------------------------ #

    async def _worker(self, index: int) -> None:
        while not self._stop.is_set():
            if self._paused:
                await asyncio.sleep(2)
                continue

            task_id = self._claim_next()
            if task_id is None:
                await asyncio.sleep(3)
                continue

            try:
                await self._process(task_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Воркер %d: задача %s упала", index, task_id)
                _fail_with_backoff(task_id, str(exc))

    def _claim_next(self) -> int | None:
        """Забирает следующую задачу, помечая её как выполняющуюся."""
        now = datetime.utcnow()
        with session_scope() as session:
            task = session.execute(
                select(DownloadTask)
                .where(
                    DownloadTask.status == TaskStatus.QUEUED,
                    (DownloadTask.retry_after.is_(None)) | (DownloadTask.retry_after <= now),
                )
                .order_by(DownloadTask.created_at)
                .limit(1)
            ).scalar_one_or_none()
            if task is None:
                return None
            task.status = TaskStatus.DOWNLOADING
            return task.id

    async def _process(self, task_id: int) -> None:
        with session_scope() as session:
            task = session.get(DownloadTask, task_id)
            if task is None:
                return
            snapshot = {
                "translation_id": task.translation_id,
                "series_id": task.series_id,
                "episode": task.episode,
                "kind": task.kind,
                "requested_height": task.requested_height,
                "authors": task.authors,
                "lang": task.lang,
                "type_kind": task.type_kind,
                "series_title": task.series_title,
            }

        if already_downloaded(snapshot["translation_id"]):
            log.info("Перевод %s уже есть локально", snapshot["translation_id"])
            _finish(task_id, TaskStatus.ALREADY_EXISTS)
            return

        # Папки может не быть вовсе — тогда создаём её по названию тайтла.
        target = ensure_target_folder(snapshot["series_id"])
        if target is None:
            _finish(
                task_id,
                TaskStatus.FAILED,
                "Некуда складывать: тайтл не привязан к папке, и создать новую не вышло. "
                "Проверьте, что корень библиотеки доступен на запись",
            )
            return

        folder, root = target
        if not root.is_writable:
            _finish(
                task_id,
                TaskStatus.FAILED,
                f"Папка {root.path} смонтирована только для чтения — загрузка невозможна",
            )
            return

        tmp_dir = Path(settings.get_str("download_tmp"))
        # Проверяем оба тома: файл скачивается во временную папку и переезжает
        # в библиотеку, а в compose это разные монтирования. Проверка только
        # временной папки обнаруживала бы нехватку места уже после загрузки.
        problem = check_free_space(
            tmp_dir,
            Path(root.path) / folder.rel_path,
            settings.get_int("min_free_space_gb") * (1 << 30),
        )
        if problem:
            _fail_with_backoff(task_id, problem)
            return

        async with Downloader() as downloader:
            links = await downloader.resolve_links(snapshot["translation_id"])

            if snapshot["kind"] == "subtitle":
                url = links.get("subtitles_url")
                if not url:
                    # У перевода с субтитрами это означает, что они вшиты в видео.
                    _finish(
                        task_id,
                        TaskStatus.FAILED,
                        "У этого перевода субтитры вшиты в видео, отдельного файла нет",
                    )
                    return
                height = None
                extension = SUBTITLE_EXTENSION
            else:
                url, height = pick_download_url(links, snapshot["requested_height"] or 1080)
                if not url:
                    _finish(task_id, TaskStatus.FAILED, "Нет доступных ссылок для скачивания")
                    return
                extension = VIDEO_EXTENSION

            filename = self._build_name(snapshot, folder, height or 0, extension)
            tmp_path = tmp_dir / f"a365-{task_id}{extension}"

            with session_scope() as session:
                db_task = session.get(DownloadTask, task_id)
                if db_task is not None:
                    db_task.resolved_height = height
                    db_task.tmp_path = str(tmp_path)
                    db_task.target_path = str(Path(root.path) / folder.rel_path / filename)

            last_publish = 0.0

            def on_progress(progress) -> None:
                nonlocal last_publish
                with session_scope() as session:
                    db_task = session.get(DownloadTask, task_id)
                    if db_task is not None:
                        db_task.bytes_done = progress.bytes_done
                        db_task.bytes_total = progress.bytes_total
                        db_task.speed_bps = progress.speed_bps

                # Событие не на каждый чанк: при мегабайтных кусках это сотни
                # сообщений на файл, а интерфейсу хватает пары раз в секунду.
                now = monotonic()
                if now - last_publish >= 1.0:
                    last_publish = now
                    bus.publish(
                        "download.progress",
                        task_id=task_id,
                        bytes_done=progress.bytes_done,
                        bytes_total=progress.bytes_total,
                        speed_bps=round(progress.speed_bps),
                    )

            def should_stop() -> bool:
                return task_id in self._cancelled or self._paused or self._stop.is_set()

            try:
                expect = "subtitle" if snapshot["kind"] == "subtitle" else "video"
                await downloader.fetch(url, tmp_path, on_progress, should_stop, expect=expect)
            except DownloadCancelled:
                # Проверяем ДО снятия отметки: иначе отмена всегда выглядела бы паузой.
                was_cancelled = task_id in self._cancelled
                self._cancelled.discard(task_id)

                if was_cancelled:
                    # Недокачанный кусок удаляем: возобновлять отменённое незачем.
                    tmp_path.with_suffix(tmp_path.suffix + ".part").unlink(missing_ok=True)
                    _finish(task_id, TaskStatus.CANCELLED)
                else:
                    # Пауза: .part остаётся, докачаем с того же места.
                    with session_scope() as session:
                        db_task = session.get(DownloadTask, task_id)
                        if db_task is not None:
                            db_task.status = TaskStatus.PAUSED
                return
            except SubscriptionRequired as exc:
                _finish(task_id, TaskStatus.FAILED, str(exc))
                return

        # Загрузка на этом закончена. Перемещение делает отдельный воркер:
        # между дисками shutil.move копирует байты, и держать на этом воркер
        # загрузки означало бы простой сети на всё время копирования.
        # Всё нужное для перемещения уже лежит в задаче (tmp_path, target_path).
        with session_scope() as session:
            db_task = session.get(DownloadTask, task_id)
            if db_task is not None:
                db_task.status = TaskStatus.DOWNLOADED
                db_task.speed_bps = 0.0
        bus.publish("download.status", task_id=task_id, status=TaskStatus.DOWNLOADED)

    def _build_name(
        self, snapshot: dict, folder: LibraryFolder, height: int, extension: str
    ) -> str:
        episode = snapshot["episode"] or 1
        existing_video = None
        if snapshot["kind"] == "subtitle":
            existing_video = _existing_video_for_episode(folder.id, episode)

        neighbour = None
        if existing_video is None:
            from app.services.filename_rules import get_rules

            rules = get_rules()
            neighbour = pick_neighbour(_folder_filenames(folder.id), set(rules.video_ext))

        ctx = NamingContext(
            show_title=snapshot["series_title"] or "Unknown",
            season=folder.season_number,
            episode=episode,
            episode_title=None,
            authors=snapshot["authors"],
            height=height,
            lang=snapshot["lang"] or "ja",
            kind=snapshot["type_kind"] or "raw",
            extension=extension,
        )
        name = build_target_name(ctx, neighbour=neighbour, existing_video=existing_video)
        return unique_path(folder.rel_path, name, set(_folder_filenames(folder.id)))

    @staticmethod
    def _recover_interrupted() -> None:
        """Возвращает в работу задачи, застрявшие из-за остановки процесса.

        Статусы `downloading` и `postprocessing` означают «этим прямо сейчас
        занят воркер». После падения или рестарта контейнера воркеров нет, а
        метки остаются — и такие задачи не забирал никто: `_claim_next` берёт
        только `queued`. То есть загрузка, прерванная перезапуском, зависала
        навсегда, хотя обещано, что она продолжится с того же места.

        Недокачанный `.part` при этом сохраняется, поэтому возврат в очередь
        означает докачку по Range, а не загрузку заново.
        """
        with session_scope() as session:
            downloading = session.execute(
                select(DownloadTask).where(DownloadTask.status == TaskStatus.DOWNLOADING)
            ).scalars().all()
            for task in downloading:
                task.status = TaskStatus.QUEUED
                task.speed_bps = 0.0

            postprocessing = session.execute(
                select(DownloadTask).where(DownloadTask.status == TaskStatus.POSTPROCESSING)
            ).scalars().all()
            for task in postprocessing:
                # Файл уже скачан целиком — возвращаем на перемещение, не на загрузку.
                task.status = TaskStatus.DOWNLOADED

        if downloading or postprocessing:
            log.info(
                "Возобновлено после перезапуска: загрузок %d, перемещений %d",
                len(downloading),
                len(postprocessing),
            )

    def _claim_next_postprocess(self) -> tuple[int, Path, Path] | None:
        """Забирает скачанную задачу под перемещение.

        Отдельная очередь и отдельная метка статуса нужны, чтобы перемещение
        между дисками не занимало воркер загрузки: `shutil.move` там копирует
        байты, и на большом файле это минуты простоя сети.
        """
        with session_scope() as session:
            task = session.execute(
                select(DownloadTask)
                .where(DownloadTask.status == TaskStatus.DOWNLOADED)
                .order_by(DownloadTask.updated_at)
                .limit(1)
            ).scalar_one_or_none()
            if task is None:
                return None
            if not task.tmp_path or not task.target_path:
                # Такого быть не должно: пути пишутся до начала загрузки.
                # Но если задача пережила рестарт в странном состоянии, лучше
                # честно её провалить, чем молча оставить висеть в очереди.
                task.status = TaskStatus.FAILED
                task.last_error = "Потеряны пути файла — перезапустите загрузку"
                return None
            task.status = TaskStatus.POSTPROCESSING
            return task.id, Path(task.tmp_path), Path(task.target_path)

    async def _postprocess_worker(self) -> None:
        while not self._stop.is_set():
            if self._paused:
                # «Пауза» для пользователя означает «ничего не происходит».
                # Скачанное подождёт: файл целый, метка downloaded сохранена,
                # после возобновления он переедет.
                await asyncio.sleep(2)
                continue

            claimed = self._claim_next_postprocess()
            if claimed is None:
                await asyncio.sleep(2)
                continue

            task_id, tmp_path, target_path = claimed
            bus.publish("download.status", task_id=task_id, status=TaskStatus.POSTPROCESSING)
            try:
                await self._postprocess(task_id, tmp_path, target_path.parent, target_path.name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Постобработка задачи %s упала", task_id)
                _fail_with_backoff(task_id, str(exc))

    async def _postprocess(
        self, task_id: int, tmp_path: Path, target_dir: Path, filename: str
    ) -> None:
        """Перемещение в библиотеку и выставление прав."""
        try:
            # Последний рубеж перед записью. Имя автора перевода приходит из
            # внешнего API и попадает в имя файла, а корень и rel_path — из БД:
            # сверяем итог с настроенным корнем, а не доверяем цепочке склеек.
            destination = resolve_within(target_dir, filename)
        except UnsafePathError as exc:
            _fail_with_backoff(task_id, str(exc))
            log.error("Отклонена запись за пределы библиотеки: %s", exc)
            return

        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            # shutil.move переживает перемещение между дисками, os.rename — нет,
            # а временная папка вполне может лежать не там же, где библиотека.
            await asyncio.to_thread(shutil.move, str(tmp_path), str(destination))
            await asyncio.to_thread(self._apply_ownership, destination)
        except OSError as exc:
            _fail_with_backoff(task_id, f"Не удалось переместить файл: {exc}")
            return

        _finish(task_id, TaskStatus.DONE)
        log.info("Готово: %s", destination.name)

    @staticmethod
    def _apply_ownership(path: Path) -> None:
        """Права на созданный файл, чтобы Jellyfin мог его прочитать."""
        import os

        try:
            umask = int(settings.get_str("umask"), 8)
            os.chmod(path, 0o666 & ~umask)
        except (OSError, ValueError) as exc:
            log.debug("Не удалось выставить права на %s: %s", path, exc)

        if hasattr(os, "chown"):
            try:
                os.chown(path, settings.get_int("puid"), settings.get_int("pgid"))
            except OSError as exc:
                # В контейнере без CAP_CHOWN это ожидаемо, если файл и так наш.
                log.debug("Не удалось сменить владельца %s: %s", path, exc)


download_queue = DownloadQueue()
