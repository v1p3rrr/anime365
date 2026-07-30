"""Очередь загрузок.

Живёт в БД, а не в памяти: очередь обязана переживать перезапуск контейнера,
иначе прерванная батч-загрузка на несколько десятков гигабайт теряется целиком.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.settings import DEFAULT_USER_ID


class TaskStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"          # файл скачан, ждёт постобработки
    POSTPROCESSING = "postprocessing"  # переименование и перемещение
    DONE = "done"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    # Такой перевод уже лежит локально — не ошибка, а штатный исход.
    ALREADY_EXISTS = "already_exists"


# Статусы, из которых задача уже не сдвинется сама.
TERMINAL_STATUSES = frozenset(
    {TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.ALREADY_EXISTS}
)

# Статусы, которые занимают воркер.
ACTIVE_STATUSES = frozenset(
    {TaskStatus.DOWNLOADING, TaskStatus.DOWNLOADED, TaskStatus.POSTPROCESSING}
)

# Значение active_key у незавершённой задачи. Ноль выбран намеренно: реальных
# идентификаторов с нулём не бывает, поэтому коллизия невозможна.
ACTIVE_KEY = 0


class DownloadTask(Base):
    __tablename__ = "download_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    translation_id: Mapped[int] = mapped_column(Integer, index=True)
    series_id: Mapped[int] = mapped_column(Integer, index=True)
    episode: Mapped[float | None] = mapped_column(Float)

    # video | subtitle
    kind: Mapped[str] = mapped_column(String(16))
    # Запрошенное качество; фактическое может оказаться ниже, если такой ступени нет.
    requested_height: Mapped[int | None] = mapped_column(Integer)
    resolved_height: Mapped[int | None] = mapped_column(Integer)

    # Для показа в очереди без джойнов.
    series_title: Mapped[str] = mapped_column(String(512), default="")
    authors: Mapped[str] = mapped_column(String(256), default="")
    lang: Mapped[str | None] = mapped_column(String(8))
    type_kind: Mapped[str | None] = mapped_column(String(16))

    status: Mapped[str] = mapped_column(String(24), default=TaskStatus.QUEUED, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    # Когда можно повторить: экспоненциальный бэкофф после падения.
    retry_after: Mapped[datetime | None] = mapped_column(DateTime)

    tmp_path: Mapped[str | None] = mapped_column(String(1024))
    target_path: Mapped[str | None] = mapped_column(String(1024))

    bytes_done: Mapped[int] = mapped_column(Integer, default=0)
    bytes_total: Mapped[int] = mapped_column(Integer, default=0)
    speed_bps: Mapped[float] = mapped_column(Float, default=0.0)

    created_by_user_id: Mapped[int] = mapped_column(Integer, default=DEFAULT_USER_ID)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    meta: Mapped[dict | None] = mapped_column(JSON)

    # Идемпотентность: два человека, нажавшие «скачать» на одном переводе с одним
    # качеством, не должны создать две задачи. Отменённые и упавшие не мешают
    # повторной постановке, поэтому ключ включает только незавершённые.
    __table_args__ = (
        UniqueConstraint(
            "translation_id", "kind", "requested_height", "active_key",
            name="uq_task_active",
        ),
    )

    # Для незавершённых задач равен 0, для завершённых — id задачи. Так уникальность
    # действует только среди активных, а история сохраняется.
    # ⚠️ Метка активности именно 0, а не 1: идентификаторы начинаются с единицы,
    # и завершённая задача №1 навсегда блокировала бы повторную постановку.
    active_key: Mapped[int] = mapped_column(Integer, default=ACTIVE_KEY)

    @property
    def progress(self) -> float:
        if not self.bytes_total:
            return 0.0
        return min(100.0, self.bytes_done / self.bytes_total * 100)


Index("ix_task_status_created", DownloadTask.status, DownloadTask.created_at)
