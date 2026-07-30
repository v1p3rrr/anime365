"""Модели локальной библиотеки и маппинга.

Маппинг устроен как many-to-many: одна папка даёт несколько series_id (по сезонам),
один series_id может лежать в нескольких папках. У каждой связи хранится, чем именно
она получена — иначе невозможно объяснить пользователю, почему тайтл смапился так.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.settings import DEFAULT_USER_ID


class LibraryRoot(Base):
    __tablename__ = "library_root"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # series | movie
    path: Mapped[str] = mapped_column(String(1024), unique=True)

    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    folder_count: Mapped[int] = mapped_column(Integer, default=0)
    # Библиотеку можно смонтировать только на чтение — это штатный режим для
    # первых прогонов. Загрузки в таком случае недоступны, и UI обязан сказать
    # об этом заранее, а не падать при первой записи.
    is_writable: Mapped[bool] = mapped_column(Boolean, default=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

    scanned_at: Mapped[datetime | None] = mapped_column(DateTime)

    folders: Mapped[list[LibraryFolder]] = relationship(
        back_populates="root", cascade="all, delete-orphan"
    )


class LibraryFolder(Base):
    """Папка тайтла или сезона."""

    __tablename__ = "library_folder"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    root_id: Mapped[int] = mapped_column(ForeignKey("library_root.id", ondelete="CASCADE"))
    # Путь относительно корня: так библиотеку можно перемонтировать в другое место,
    # не переписывая всю таблицу.
    rel_path: Mapped[str] = mapped_column(String(1024))
    name: Mapped[str] = mapped_column(String(512))

    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_folder.id", ondelete="CASCADE")
    )
    # Номер сезона для подпапки; None у папки самого тайтла.
    season_number: Mapped[int | None] = mapped_column(Integer)

    nfo: Mapped[dict | None] = mapped_column(JSON)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    mtime: Mapped[float | None] = mapped_column(Float)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    root: Mapped[LibraryRoot] = relationship(back_populates="folders")
    files: Mapped[list[LibraryFile]] = relationship(
        back_populates="folder", cascade="all, delete-orphan"
    )
    mappings: Mapped[list[TitleMapping]] = relationship(
        back_populates="folder", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("root_id", "rel_path", name="uq_folder_path"),)


class LibraryFile(Base):
    __tablename__ = "library_file"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("library_folder.id", ondelete="CASCADE"))

    filename: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16))  # video | subtitle | metadata

    lang: Mapped[str | None] = mapped_column(String(8), index=True)
    season_number: Mapped[int | None] = mapped_column(Integer)
    episode: Mapped[float | None] = mapped_column(Float, index=True)
    episode_end: Mapped[float | None] = mapped_column(Float)

    # Каким правилом разобралось имя — видно в UI и в отчёте.
    matched_rule_id: Mapped[str | None] = mapped_column(String(64))
    release_group: Mapped[str | None] = mapped_column(String(128))
    # Заполнено, если файл скачан с Anime365: не качаем такой перевод повторно.
    anime365_translation_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # Дорожки внутри контейнера из .nfo — подсказка о вшитых субтитрах и озвучке.
    streams: Mapped[dict | None] = mapped_column(JSON)

    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mtime: Mapped[float | None] = mapped_column(Float)
    # Файл не удалось отнести ни к одной серии — показываем в отчёте.
    is_unmatched: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    excluded_reason: Mapped[str | None] = mapped_column(String(128))

    folder: Mapped[LibraryFolder] = relationship(back_populates="files")


class TitleMapping(Base):
    """Связь папки с тайтлом каталога."""

    __tablename__ = "title_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), index=True
    )
    folder_id: Mapped[int] = mapped_column(
        ForeignKey("library_folder.id", ondelete="CASCADE"), index=True
    )

    # anilist_id | mal_id | original_title | exact_title | premiered | fuzzy | manual
    source: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    matched_on: Mapped[str | None] = mapped_column(Text)

    # Ручная привязка не перетирается автоматическим сканированием.
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    # Найдено нечётко и ждёт подтверждения пользователя.
    needs_confirmation: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    created_by_user_id: Mapped[int] = mapped_column(Integer, default=DEFAULT_USER_ID)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    folder: Mapped[LibraryFolder] = relationship(back_populates="mappings")

    __table_args__ = (
        UniqueConstraint("series_id", "folder_id", name="uq_mapping_series_folder"),
    )


class EpisodeMapping(Base):
    """Связь конкретной серии тайтла с конкретным локальным файлом."""

    __tablename__ = "episode_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), index=True
    )
    episode: Mapped[float] = mapped_column(Float)
    file_id: Mapped[int] = mapped_column(ForeignKey("library_file.id", ondelete="CASCADE"))

    # video | sub_ru | sub_ja | sub_en | sub_unknown
    role: Mapped[str] = mapped_column(String(16))
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_user_id: Mapped[int] = mapped_column(Integer, default=DEFAULT_USER_ID)

    __table_args__ = (
        UniqueConstraint("series_id", "episode", "role", "file_id", name="uq_episode_file"),
    )


Index("ix_episode_mapping_series_ep", EpisodeMapping.series_id, EpisodeMapping.episode)
Index("ix_library_file_folder_kind", LibraryFile.folder_id, LibraryFile.kind)
