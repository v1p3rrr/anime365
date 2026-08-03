"""Эндпоинты очереди загрузок."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core import errors
from app.core.errors import ApiError
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import rate_limit
from app.core.db import session_scope
from app.core.permissions import Permission, require
from app.models.catalog import Episode, Translation
from app.models.downloads import (
    ACTIVE_KEY,
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    DownloadTask,
    TaskStatus,
)
from app.models.library import LibraryFolder, LibraryRoot, TitleMapping
from app.services.anime365 import Anime365Error, SubscriptionRequired
from app.services.download_queue import QueueError, download_queue, enqueue
from app.services.title_service import cached_has_subtitles, get_embed

log = logging.getLogger(__name__)

router = APIRouter(prefix="/downloads", tags=["downloads"])


class EnqueueRequest(BaseModel):
    translation_id: int
    # audio — извлечение звуковой дорожки: файл уходит не в библиотеку,
    # а в отдельный корень фонотеки.
    kind: str = Field("video", pattern="^(video|subtitle|audio)$")
    height: int | None = None


class BatchRequest(BaseModel):
    translation_ids: list[int] = Field(min_length=1, max_length=2000)
    kind: str = Field("video", pattern="^(video|subtitle|both|audio)$")
    height: int | None = None
    #: Пропустить серии, у которых нужное уже лежит локально.
    #:
    #: Отбор идёт на сервере, а не на клиенте: «что уже есть» он и так считает
    #: для списка серий, и вторая копия этой логики на фронте неизбежно
    #: разошлась бы с первой.
    only_missing: bool = False


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
        raise ApiError(
            409,
            errors.ROOT_READ_ONLY,
            f"Папка {root.path} смонтирована только для чтения — загрузка невозможна. "
            "Перемонтируйте том без флага :ro",
            path=root.path,
        )


# Вшитые субтитры растеризованы в кадры: отдельной дорожки в файле нет
# (проверено ffprobe — только video и audio), извлечь их нечем. Значит отказ
# окончательный, а не «попробуйте позже», и интерфейс вправе погасить кнопку
# навсегда. Код нужен, чтобы фронт опознал случай, не разбирая текст.
HARDSUB_CODE = "hardsub"
HARDSUB_MESSAGE = (
    "У этого перевода субтитры вшиты в видео — отдельного файла не существует. "
    "Скачайте видео: субтитры уже в картинке."
)


async def _reject_hardsub(translation_id: int) -> None:
    """Отказывает сразу, если у перевода нет файла субтитров.

    Проверяем на постановке, а не в воркере: иначе задача уходит в очередь и
    падает через несколько секунд, что выглядит как случайный сбой. Здесь же
    пользователь получает объяснение в ответ на своё нажатие.

    Один запрос к API на неизвестный перевод — приемлемо: это явное действие,
    а повторные нажатия попадут уже в кэш.
    """
    try:
        info = await get_embed(translation_id)
    except (Anime365Error, SubscriptionRequired):
        # Не смогли проверить — пропускаем дальше. Задача упадёт в воркере со
        # своим сообщением, и это лучше, чем отказать по недоказанной причине.
        return

    if not info["has_subtitles"]:
        raise ApiError(409, errors.HARDSUB, HARDSUB_MESSAGE)


@router.post(
    "",
    dependencies=[Depends(require(Permission.DOWNLOADS))],
)
async def create_task(payload: EnqueueRequest) -> dict[str, Any]:
    if payload.kind == "subtitle":
        await _reject_hardsub(payload.translation_id)

    try:
        result = enqueue(payload.translation_id, payload.kind, payload.height)
    except QueueError as exc:
        raise ApiError(400, errors.QUEUE_REJECTED, str(exc), reason=str(exc)) from exc

    # Звук уходит в корень фонотеки, а не в библиотеку: её доступность
    # на запись для такой задачи значения не имеет.
    if payload.kind != "audio":
        _check_target_writable(_series_of(payload.translation_id))
    return result


def _series_of(translation_id: int) -> int:
    from app.models.catalog import Translation

    with session_scope() as session:
        translation = session.get(Translation, translation_id)
        return translation.series_id if translation else 0


def _already_local(translation_ids: list[int]) -> dict[int, set[str]]:
    """Что из запрошенного уже лежит локально — по каждому переводу.

    Ключ — id перевода, значение — набор ролей (`video`, `subtitle`), которые
    для его серии уже есть на диске.

    Роли локальных файлов разложены по языкам (`sub_ru`, `sub_ja`, …), а батч
    оперирует видом загрузки. Субтитры любого языка считаем закрывающими
    «субтитры»: батч ставится на конкретного автора и язык, и если субтитры к
    этой серии уже есть, повторная закачка того же языка — почти всегда не то,
    чего хотят, нажимая «скачать отсутствующие».
    """
    from app.services.title_service import get_local_files

    with session_scope() as session:
        rows = session.execute(
            select(Translation.id, Translation.series_id, Episode.episode_num)
            .join(Episode, Translation.episode_id == Episode.id)
            .where(Translation.id.in_(translation_ids))
        ).all()

    by_series: dict[int, dict[float, set[str]]] = {}
    out: dict[int, set[str]] = {}
    for translation_id, series_id, episode_num in rows:
        if episode_num is None:
            continue
        if series_id not in by_series:
            local: dict[float, set[str]] = {}
            for item in get_local_files(series_id):
                role = item["role"]
                local.setdefault(item["episode"], set()).add(
                    "video" if role == "video" else "subtitle"
                )
            by_series[series_id] = local
        roles = by_series[series_id].get(episode_num)
        if roles:
            out[translation_id] = roles
    return out


@router.post(
    "/batch",
    # Один батч — до 2000 задач. Десяток таких подряд забьёт диск быстрее,
    # чем пользователь успеет заметить, поэтому частота ограничена.
    dependencies=[Depends(rate_limit("batch", limit=20, window=60))],
)
def create_batch(payload: BatchRequest) -> dict[str, Any]:
    """Пакетная постановка: «все видео», «все субтитры», «видео и субтитры»."""
    kinds = ["video", "subtitle"] if payload.kind == "both" else [payload.kind]

    created = skipped = failed = hardsub = 0
    already = 0
    errors: list[str] = []

    have = _already_local(payload.translation_ids) if payload.only_missing else {}

    for translation_id in payload.translation_ids:
        for kind in kinds:
            if payload.only_missing and kind in have.get(translation_id, ()):
                already += 1
                skipped += 1
                continue
            # Про вшитые субтитры спрашиваем только кэш: в батче переводов
            # бывают сотни, и запрос к API на каждый превратил бы постановку
            # очереди в многоминутную операцию. Непроверенные уйдут в очередь
            # и упадут по одному — это по-прежнему честнее, чем задержка.
            if kind == "subtitle" and cached_has_subtitles(translation_id) is False:
                hardsub += 1
                skipped += 1
                continue
            try:
                result = enqueue(translation_id, kind, payload.height)
                created += 1 if result["created"] else 0
                skipped += 0 if result["created"] else 1
            except QueueError as exc:
                failed += 1
                if len(errors) < 10:
                    errors.append(f"{translation_id}: {exc}")

    return {
        "created": created,
        "skipped": skipped,
        "failed": failed,
        # Отдельным числом, а не в общей куче пропущенных: «эти субтитры скачать
        # нельзя в принципе» — не то же самое, что «уже в очереди».
        "hardsub_skipped": hardsub,
        # Тоже отдельным числом: «это уже лежит на диске» — не ошибка и не
        # «уже в очереди», и в сводке это должно читаться как успех.
        "already_local": already,
        "errors": errors,
    }


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


@router.post(
    "/{task_id}/retry",
    dependencies=[Depends(require(Permission.DOWNLOADS))],
)
def retry_task(task_id: int) -> dict[str, Any]:
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            raise ApiError(404, errors.TASK_NOT_FOUND, "Задача не найдена")
        task.status = TaskStatus.QUEUED
        task.attempts = 0
        task.retry_after = None
        task.last_error = None
        task.finished_at = None
        task.active_key = ACTIVE_KEY
    return {"id": task_id, "status": TaskStatus.QUEUED}


@router.post(
    "/{task_id}/pause",
    dependencies=[Depends(require(Permission.DOWNLOADS))],
)
def pause_task(task_id: int) -> dict[str, Any]:
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            raise ApiError(404, errors.TASK_NOT_FOUND, "Задача не найдена")
        if task.status == TaskStatus.QUEUED:
            task.status = TaskStatus.PAUSED
        elif task.status == TaskStatus.DOWNLOADING:
            # Воркер увидит остановку и сам переведёт в paused, сохранив .part.
            download_queue.cancel(task_id)
    return {"id": task_id, "paused": True}


@router.post(
    "/{task_id}/resume",
    dependencies=[Depends(require(Permission.DOWNLOADS))],
)
def resume_task(task_id: int) -> dict[str, Any]:
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            raise ApiError(404, errors.TASK_NOT_FOUND, "Задача не найдена")
        if task.status == TaskStatus.PAUSED:
            task.status = TaskStatus.QUEUED
            task.retry_after = None
    return {"id": task_id, "resumed": True}


@router.post(
    "/{task_id}/cancel",
    dependencies=[Depends(require(Permission.DOWNLOADS))],
)
def cancel_task(task_id: int) -> dict[str, Any]:
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            raise ApiError(404, errors.TASK_NOT_FOUND, "Задача не найдена")
        if task.status in ACTIVE_STATUSES:
            download_queue.cancel(task_id)
        else:
            task.status = TaskStatus.CANCELLED
            task.finished_at = datetime.utcnow()
            task.active_key = task.id
    return {"id": task_id, "cancelled": True}


@router.post(
    "/pause-all",
    dependencies=[Depends(require(Permission.DOWNLOADS))],
)
def pause_all() -> dict[str, Any]:
    download_queue.pause_all()
    return {"paused": True}


@router.post(
    "/resume-all",
    dependencies=[Depends(require(Permission.DOWNLOADS))],
)
def resume_all() -> dict[str, Any]:
    download_queue.resume_all()
    return {"paused": False}


@router.post(
    "/clear-finished",
    dependencies=[Depends(require(Permission.DOWNLOADS))],
)
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


@router.post(
    "/{task_id}/requeue",
    dependencies=[Depends(require(Permission.DOWNLOADS))],
)
def requeue_task(task_id: int) -> dict[str, Any]:
    """Отправляет задачу в конец очереди.

    Нужно, когда одна тяжёлая загрузка держит очередь, а хочется пропустить
    вперёд остальные. Раньше выбора не было вовсе: порядок определялся временем
    постановки и не менялся.

    Активную задачу сначала останавливаем — иначе она продолжит качаться, и
    «в конец» ничего не изменит. Уже скачанные байты остаются: докачка идёт по
    Range, поэтому возврат к задаче не начнёт всё заново.
    """
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            raise ApiError(404, errors.TASK_NOT_FOUND, "Задача не найдена")
        if task.status in TERMINAL_STATUSES:
            raise ApiError(
                400,
                errors.TASK_NOT_ACTIVE,
                "Задача уже завершена — двигать её в очереди нечего",
            )
        was_active = task.status in ACTIVE_STATUSES

    if was_active:
        download_queue.cancel(task_id)

    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            raise ApiError(404, errors.TASK_NOT_FOUND, "Задача не найдена")
        task.status = TaskStatus.QUEUED
        task.queued_at = datetime.utcnow()
        task.retry_after = None
        task.last_error = None
        task.finished_at = None
        task.active_key = ACTIVE_KEY
    return {"id": task_id, "status": TaskStatus.QUEUED}


@router.delete(
    "/{task_id}",
    dependencies=[Depends(require(Permission.DOWNLOADS))],
)
def delete_task(task_id: int) -> dict[str, Any]:
    """Убирает одну задачу из списка.

    До этого удалить можно было только всё завершённое разом, и ошибочные
    задачи не удалялись вовсе — а именно они и копятся: перевод со вшитыми
    субтитрами будет падать при каждой попытке, и висеть в списке вечно.

    Активную задачу сначала останавливаем: удалить запись из-под работающего
    воркера значило бы оставить его писать во временный файл, о котором уже
    некому вспомнить.
    """
    from sqlalchemy import delete as sql_delete

    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            raise ApiError(404, errors.TASK_NOT_FOUND, "Задача не найдена")
        was_active = task.status in ACTIVE_STATUSES

    if was_active:
        download_queue.cancel(task_id)

    with session_scope() as session:
        session.execute(sql_delete(DownloadTask).where(DownloadTask.id == task_id))
    return {"id": task_id, "deleted": True}


@router.post(
    "/clear-failed",
    dependencies=[Depends(require(Permission.DOWNLOADS))],
)
def clear_failed() -> dict[str, Any]:
    """Убирает из списка все задачи с ошибкой.

    Отдельно от `clear-finished`: то — про историю успешных, а это про разбор
    завалов. Часть ошибок повторяется при каждой попытке (вшитые субтитры,
    удалённый перевод), и без такой кнопки список только растёт.
    """
    from sqlalchemy import delete as sql_delete

    with session_scope() as session:
        result = session.execute(
            sql_delete(DownloadTask).where(DownloadTask.status == TaskStatus.FAILED)
        )
    return {"deleted": result.rowcount}
