"""Фонотека: что извлечено, где лежит и можно ли это слушать.

Дорожки отдельной таблицей не описаны намеренно. Источник правды — завершённая
задача загрузки с `kind = "audio"`: в ней уже есть тайтл, серия, автор, язык и
путь к файлу. Заводить рядом вторую запись значило бы держать две правды об
одном файле и однажды их разойтись.

⚠️ Файл на диске проверяем **всегда**. Запись о задаче переживает удаление файла
руками, и без проверки список предлагал бы слушать то, чего нет, — а узнать об
этом можно было бы только нажав «играть».
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.downloads import DownloadTask, TaskStatus

log = logging.getLogger(__name__)

#: Что браузер умеет проигрывать сам. Извлечение отдаёт эти же контейнеры, но
#: список нужен отдельно: в фонотеке может лежать и `.mka`, если у источника был
#: экзотический кодек, — такую дорожку предлагать «слушать» нечестно.
PLAYABLE_SUFFIXES = {".m4a", ".mp3", ".opus", ".ogg", ".aac", ".flac", ".wav"}

CONTENT_TYPES = {
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".mp3": "audio/mpeg",
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".mka": "audio/x-matroska",
}


def content_type_for(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _row_to_track(task: DownloadTask, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "task_id": task.id,
        "translation_id": task.translation_id,
        "series_id": task.series_id,
        "series_title": task.series_title,
        "episode": task.episode,
        "authors": task.authors,
        "lang": task.lang,
        "filename": path.name,
        "size_bytes": stat.st_size,
        # Проигрывать умеет браузер, а не мы: `.mka` он не откроет, и кнопку
        # воспроизведения у такой дорожки показывать не надо.
        "playable": path.suffix.lower() in PLAYABLE_SUFFIXES,
        "created_at": task.finished_at.isoformat() if task.finished_at else None,
    }


def _completed_audio_tasks(series_id: int | None = None) -> list[DownloadTask]:
    with session_scope() as session:
        stmt = select(DownloadTask).where(
            DownloadTask.kind == "audio",
            DownloadTask.status.in_([TaskStatus.DONE, TaskStatus.ALREADY_EXISTS]),
            DownloadTask.target_path.is_not(None),
        )
        if series_id is not None:
            stmt = stmt.where(DownloadTask.series_id == series_id)
        rows = session.execute(stmt.order_by(DownloadTask.episode, DownloadTask.id)).scalars().all()
        for row in rows:
            session.expunge(row)
        return list(rows)


def tracks_for_series(series_id: int) -> list[dict[str, Any]]:
    """Дорожки одного тайтла, по возрастанию номера серии."""
    out = []
    for task in _completed_audio_tasks(series_id):
        path = Path(task.target_path or "")
        if path.is_file():
            out.append(_row_to_track(task, path))
    return out


def library() -> list[dict[str, Any]]:
    """Вся фонотека, сгруппированная по тайтлам.

    Группировка на сервере, а не на клиенте: постер и название тайтла всё равно
    приходится доставать из каталога, и делать это в цикле по строкам на фронте
    значило бы столько же запросов, сколько дорожек.
    """
    tasks = _completed_audio_tasks()

    by_series: dict[int, list[dict[str, Any]]] = {}
    for task in tasks:
        path = Path(task.target_path or "")
        if not path.is_file():
            continue
        by_series.setdefault(task.series_id, []).append(_row_to_track(task, path))

    if not by_series:
        return []

    with session_scope() as session:
        posters = {
            row.id: (row.poster_url, dict(row.titles or {}))
            for row in session.execute(
                select(Series).where(Series.id.in_(by_series.keys()))
            ).scalars()
        }

    out = []
    for series_id, tracks in by_series.items():
        poster, titles = posters.get(series_id, (None, {}))
        out.append(
            {
                "series_id": series_id,
                "titles": titles,
                "poster_url": poster,
                # Название из задачи — запасное: тайтл мог пропасть из каталога,
                # а дорожка осталась, и показывать её безымянной незачем.
                "fallback_title": tracks[0]["series_title"],
                "track_count": len(tracks),
                "size_bytes": sum(track["size_bytes"] for track in tracks),
                "tracks": tracks,
            }
        )

    out.sort(key=lambda item: item["fallback_title"].casefold())
    return out


def track_path(task_id: int) -> Path | None:
    """Путь к файлу дорожки — для отдачи потоком.

    ⚠️ Путь берём из записи задачи, а НЕ из параметра запроса. Принять путь от
    клиента здесь означало бы отдать наружу чтение любого файла на сервере;
    идентификатор задачи такой возможности не даёт.
    """
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None or task.kind != "audio" or not task.target_path:
            return None
        if task.status not in (TaskStatus.DONE, TaskStatus.ALREADY_EXISTS):
            return None
        path = Path(task.target_path)
    return path if path.is_file() else None
