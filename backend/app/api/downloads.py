"""Эндпоинты очереди загрузок."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import rate_limit
from app.core.db import session_scope
from app.models.downloads import ACTIVE_KEY, ACTIVE_STATUSES, DownloadTask, TaskStatus
from app.models.library import LibraryFolder, LibraryRoot, TitleMapping
from app.services.download_queue import QueueError, download_queue, enqueue

log = logging.getLogger(__name__)

router = APIRouter(prefix="/downloads", tags=["downloads"])


class EnqueueRequest(BaseModel):
    translation_id: int
    kind: str = Field("video", pattern="^(video|subtitle)$")
    height: int | None = None


class BatchRequest(BaseModel):
    translation_ids: list[int] = Field(min_length=1, max_length=2000)
    kind: str = Field("video", pattern="^(video|subtitle|both)$")
    height: int | None = None


def _task_dict(task: DownloadTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "translation_id": task.translation_id,
        "series_id": task.series_id,
        "series_title": task.series_title,
        "episode": task.episode,
        "kind": task.kind,
        "authors": task.authors,
        "lang": task.lang,
        "requested_height": task.requested_height,
        "resolved_height": task.resolved_height,
        "status": task.status,
        "attempts": task.attempts,
        "last_error": task.last_error,
        "bytes_done": task.bytes_done,
        "bytes_total": task.bytes_total,
        "speed_bps": task.speed_bps,
        "progress": round(task.progress, 1),
        "target_path": task.target_path,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


def _check_target_writable(series_id: int) -> None:
    """Отказываем сразу, если целевая папка только для чтения.

    Иначе задача встанет в очередь и упадёт уже в процессе — это выглядит как
    непонятный сбой вместо предсказуемого отказа с объяснением.
    """
    with session_scope() as session:
        root = session.execute(
            select(LibraryRoot)
            .join(LibraryFolder, LibraryFolder.root_id == LibraryRoot.id)
            .join(TitleMapping, TitleMapping.folder_id == LibraryFolder.id)
            .where(
                TitleMapping.series_id == series_id,
                TitleMapping.is_primary.is_(True),
            )
            .limit(1)
        ).scalar_one_or_none()

    if root is not None and not root.is_writable:
        raise HTTPException(
            409,
            f"Папка {root.path} смонтирована только для чтения — загрузка невозможна. "
            "Перемонтируйте том без флага :ro",
        )


@router.post("")
def create_task(payload: EnqueueRequest) -> dict[str, Any]:
    try:
        result = enqueue(payload.translation_id, payload.kind, payload.height)
    except QueueError as exc:
        raise HTTPException(400, str(exc)) from exc

    _check_target_writable(_series_of(payload.translation_id))
    return result


def _series_of(translation_id: int) -> int:
    from app.models.catalog import Translation

    with session_scope() as session:
        translation = session.get(Translation, translation_id)
        return translation.series_id if translation else 0


@router.post(
    "/batch",
    # Один батч — до 2000 задач. Десяток таких подряд забьёт диск быстрее,
    # чем пользователь успеет заметить, поэтому частота ограничена.
    dependencies=[Depends(rate_limit("batch", limit=20, window=60))],
)
def create_batch(payload: BatchRequest) -> dict[str, Any]:
    """Пакетная постановка: «все видео», «все субтитры», «видео и субтитры»."""
    kinds = ["video", "subtitle"] if payload.kind == "both" else [payload.kind]

    created = skipped = failed = 0
    errors: list[str] = []

    for translation_id in payload.translation_ids:
        for kind in kinds:
            try:
                result = enqueue(translation_id, kind, payload.height)
                created += 1 if result["created"] else 0
                skipped += 0 if result["created"] else 1
            except QueueError as exc:
                failed += 1
                if len(errors) < 10:
                    errors.append(f"{translation_id}: {exc}")

    return {"created": created, "skipped": skipped, "failed": failed, "errors": errors}


@router.get("")
def list_tasks(
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    with session_scope() as session:
        stmt = select(DownloadTask).order_by(DownloadTask.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(DownloadTask.status == status)
        tasks = session.execute(stmt).scalars().all()

        counts = {
            status: count
            for status, count in session.execute(
                select(DownloadTask.status, func.count(DownloadTask.id)).group_by(
                    DownloadTask.status
                )
            ).all()
        }

        return {
            "paused": download_queue.paused,
            "counts": counts,
            "tasks": [_task_dict(t) for t in tasks],
        }


@router.post("/{task_id}/retry")
def retry_task(task_id: int) -> dict[str, Any]:
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        task.status = TaskStatus.QUEUED
        task.attempts = 0
        task.retry_after = None
        task.last_error = None
        task.finished_at = None
        task.active_key = ACTIVE_KEY
    return {"id": task_id, "status": TaskStatus.QUEUED}


@router.post("/{task_id}/pause")
def pause_task(task_id: int) -> dict[str, Any]:
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        if task.status == TaskStatus.QUEUED:
            task.status = TaskStatus.PAUSED
        elif task.status == TaskStatus.DOWNLOADING:
            # Воркер увидит остановку и сам переведёт в paused, сохранив .part.
            download_queue.cancel(task_id)
    return {"id": task_id, "paused": True}


@router.post("/{task_id}/resume")
def resume_task(task_id: int) -> dict[str, Any]:
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        if task.status == TaskStatus.PAUSED:
            task.status = TaskStatus.QUEUED
            task.retry_after = None
    return {"id": task_id, "resumed": True}


@router.post("/{task_id}/cancel")
def cancel_task(task_id: int) -> dict[str, Any]:
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        if task.status in ACTIVE_STATUSES:
            download_queue.cancel(task_id)
        else:
            task.status = TaskStatus.CANCELLED
            task.finished_at = datetime.utcnow()
            task.active_key = task.id
    return {"id": task_id, "cancelled": True}


@router.post("/pause-all")
def pause_all() -> dict[str, Any]:
    download_queue.pause_all()
    return {"paused": True}


@router.post("/resume-all")
def resume_all() -> dict[str, Any]:
    download_queue.resume_all()
    return {"paused": False}


@router.post("/clear-finished")
def clear_finished() -> dict[str, Any]:
    """Убирает из списка завершённые задачи — историю чистить полезно."""
    from sqlalchemy import delete

    with session_scope() as session:
        result = session.execute(
            delete(DownloadTask).where(
                DownloadTask.status.in_(
                    [TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.ALREADY_EXISTS]
                )
            )
        )
    return {"deleted": result.rowcount}
