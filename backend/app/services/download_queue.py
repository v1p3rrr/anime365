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
from app.models.catalog import Series, Translation
from app.models.downloads import ACTIVE_KEY, ACTIVE_STATUSES, DownloadTask, TaskStatus
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot, TitleMapping
from app.services.anime365 import Anime365Error, SubscriptionRequired
from app.services.downloader import (
    DownloadCancelled,
    Downloader,
    free_space_bytes,
    pick_download_url,
)
from app.services.naming import NamingContext, build_target_name, pick_neighbour, unique_path

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

        self._stop.clear()
        for index in range(workers):
            self._workers.append(asyncio.create_task(self._worker(index)))
        log.info("Очередь загрузок запущена: воркеров %d", workers)

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

        target = _primary_folder(snapshot["series_id"])
        if target is None:
            _finish(task_id, TaskStatus.FAILED, "Тайтл не привязан ни к одной локальной папке")
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
        min_free = settings.get_int("min_free_space_gb") * (1 << 30)
        if free_space_bytes(tmp_dir) < min_free:
            _fail_with_backoff(task_id, "Недостаточно свободного места")
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

        await self._postprocess(task_id, tmp_path, Path(root.path) / folder.rel_path, filename)

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

    async def _postprocess(
        self, task_id: int, tmp_path: Path, target_dir: Path, filename: str
    ) -> None:
        """Перемещение в библиотеку и выставление прав."""
        with session_scope() as session:
            db_task = session.get(DownloadTask, task_id)
            if db_task is not None:
                db_task.status = TaskStatus.POSTPROCESSING

        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / filename

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
