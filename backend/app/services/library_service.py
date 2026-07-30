"""Сохранение результатов сканирования и автоматический маппинг."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import session_scope
from app.models.catalog import ScanRun, Series
from app.models.library import (
    EpisodeMapping,
    LibraryFile,
    LibraryFolder,
    LibraryRoot,
    TitleMapping,
)
from app.services.filename_rules import get_rules
from app.services.library_scan import RootScanResult, ScannedFolder, scan_root, summarize
from app.services.matcher import MatchContext, Matcher, SeriesCandidate, TitleIndex

log = logging.getLogger(__name__)

# Роль файла в маппинге эпизода.
SUB_ROLE = {"ru": "sub_ru", "ja": "sub_ja", "en": "sub_en"}


def build_index() -> TitleIndex:
    with session_scope() as session:
        rows = session.execute(select(Series)).scalars().all()
        candidates = [
            SeriesCandidate(
                id=r.id,
                titles=r.titles or {},
                all_titles=r.all_titles or [],
                type=r.type,
                year=r.year,
                season=r.season,
                number_of_episodes=r.number_of_episodes,
                mal_id=r.mal_id,
                anilist_id=r.anilist_id,
            )
            for r in rows
        ]
    return TitleIndex(candidates)


def _persist_folder(
    session: Session,
    root_id: int,
    folder: ScannedFolder,
    parent_id: int | None = None,
) -> LibraryFolder:
    existing = session.execute(
        select(LibraryFolder).where(
            LibraryFolder.root_id == root_id, LibraryFolder.rel_path == folder.rel_path
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = LibraryFolder(root_id=root_id, rel_path=folder.rel_path)
        session.add(existing)

    existing.name = folder.name
    existing.parent_id = parent_id
    existing.season_number = folder.season_number
    existing.nfo = folder.nfo.to_dict() if folder.nfo else None
    existing.size_bytes = folder.size_bytes
    existing.video_count = folder.video_count
    existing.mtime = folder.mtime
    existing.scanned_at = datetime.utcnow()
    session.flush()

    # Файлы пересобираем целиком: они дешёвые, а отслеживать удаления по одному
    # сложнее, чем переписать список.
    session.execute(delete(LibraryFile).where(LibraryFile.folder_id == existing.id))
    for file in folder.files:
        session.add(
            LibraryFile(
                folder_id=existing.id,
                filename=file.filename,
                kind=file.kind,
                lang=file.lang,
                season_number=file.season_number,
                episode=file.episode,
                episode_end=file.episode_end,
                matched_rule_id=file.matched_rule_id,
                release_group=file.release_group,
                anime365_translation_id=file.anime365_translation_id,
                streams=file.streams,
                size_bytes=file.size_bytes,
                mtime=file.mtime,
                is_unmatched=file.is_unmatched,
                excluded_reason=file.excluded_reason,
            )
        )

    for child in folder.children:
        _persist_folder(session, root_id, child, parent_id=existing.id)

    return existing


def _persist_root(result: RootScanResult) -> int:
    with session_scope() as session:
        root = session.execute(
            select(LibraryRoot).where(LibraryRoot.path == result.path)
        ).scalar_one_or_none()
        if root is None:
            root = LibraryRoot(path=result.path, kind=result.kind)
            session.add(root)

        root.kind = result.kind
        root.is_available = result.is_available
        root.is_writable = result.is_writable
        root.total_bytes = result.total_bytes
        root.folder_count = len(result.folders)
        root.scanned_at = datetime.utcnow()
        session.flush()
        root_id = root.id

        for folder in result.folders:
            _persist_folder(session, root_id, folder)

    return root_id


def _map_folders(root_id: int, index: TitleIndex, scope: str) -> dict[str, int]:
    """Проставляет маппинг для всех папок корня.

    Сезонные подпапки мапятся отдельно от папки тайтла: в Anime365 каждый сезон
    это отдельный тайтл. Ручные привязки не трогаются.
    """
    matcher = Matcher(
        index,
        auto_threshold=settings.get_int("fuzzy_auto_threshold"),
        suggest_threshold=settings.get_int("fuzzy_suggest_threshold"),
    )
    stats = {"auto": 0, "suggested": 0, "unmatched": 0, "manual_kept": 0}

    with session_scope() as session:
        folders = (
            session.execute(select(LibraryFolder).where(LibraryFolder.root_id == root_id))
            .scalars()
            .all()
        )
        by_id = {f.id: f for f in folders}

        for folder in folders:
            manual = session.execute(
                select(TitleMapping).where(
                    TitleMapping.folder_id == folder.id, TitleMapping.is_manual.is_(True)
                )
            ).scalars().all()
            if manual:
                # Ручная привязка — решение пользователя, автоскан её не перетирает.
                stats["manual_kept"] += 1
                continue

            parent = by_id.get(folder.parent_id) if folder.parent_id else None
            # Папка сезона берёт название и внешние ID у родителя.
            title_nfo = _nfo_of(parent) if parent is not None else _nfo_of(folder)
            season_nfo = _nfo_of(folder) if parent is not None else None
            folder_name = parent.name if parent is not None else folder.name

            # Папка тайтла без видео и без подпапок с видео — мапить нечего,
            # но сама привязка полезна (пустые папки в библиотеке встречаются).
            ctx = MatchContext(
                folder_name=folder_name,
                scope=scope,
                nfo=title_nfo,
                season_nfo=season_nfo,
                local_episode_count=folder.video_count,
            )
            result = matcher.match(ctx)

            session.execute(
                delete(TitleMapping).where(
                    TitleMapping.folder_id == folder.id, TitleMapping.is_manual.is_(False)
                )
            )

            if result is None:
                stats["unmatched"] += 1
                continue

            session.add(
                TitleMapping(
                    series_id=result.series_id,
                    folder_id=folder.id,
                    source=result.source.value,
                    confidence=result.confidence,
                    matched_on=result.matched_on,
                    is_manual=False,
                    needs_confirmation=result.needs_confirmation,
                )
            )
            stats["suggested" if result.needs_confirmation else "auto"] += 1

    return stats


def _nfo_of(folder: LibraryFolder | None):
    """Восстанавливает NfoData из сохранённого JSON."""
    if folder is None or not folder.nfo:
        return None

    from app.services.nfo_parser import NfoData, StreamInfo

    raw = folder.nfo
    data = NfoData(kind=raw.get("kind", "tvshow"), path=raw.get("path", ""))
    for key in (
        "title",
        "original_title",
        "show_title",
        "year",
        "premiered",
        "season",
        "episode",
        "anilist_id",
        "shikimori_id",
        "tvdb_id",
        "tmdb_id",
        "imdb_id",
        "kitsu_id",
    ):
        setattr(data, key, raw.get(key))
    streams = raw.get("streams") or {}
    data.streams = StreamInfo(
        subtitle_languages=streams.get("subtitle_languages") or [],
        audio_languages=streams.get("audio_languages") or [],
    )
    return data


def _map_episodes(root_id: int) -> int:
    """Связывает локальные файлы с сериями смапленных тайтлов."""
    linked = 0
    with session_scope() as session:
        rows = session.execute(
            select(TitleMapping, LibraryFolder)
            .join(LibraryFolder, TitleMapping.folder_id == LibraryFolder.id)
            .where(LibraryFolder.root_id == root_id)
        ).all()

        for mapping, folder in rows:
            files = (
                session.execute(select(LibraryFile).where(LibraryFile.folder_id == folder.id))
                .scalars()
                .all()
            )
            session.execute(
                delete(EpisodeMapping).where(
                    EpisodeMapping.series_id == mapping.series_id,
                    EpisodeMapping.is_manual.is_(False),
                    EpisodeMapping.file_id.in_([f.id for f in files]) if files else False,
                )
            )

            for file in files:
                if file.episode is None or file.excluded_reason:
                    continue
                if file.kind == "video":
                    role = "video"
                elif file.kind == "subtitle":
                    role = SUB_ROLE.get(file.lang or "", "sub_unknown")
                else:
                    continue

                session.add(
                    EpisodeMapping(
                        series_id=mapping.series_id,
                        episode=file.episode,
                        file_id=file.id,
                        role=role,
                        is_manual=False,
                    )
                )
                linked += 1

    return linked


class LibraryScanner:
    def __init__(self) -> None:
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def scan_all(self) -> dict[str, Any]:
        if self._running:
            raise RuntimeError("Сканирование уже идёт")

        self._running = True
        with session_scope() as session:
            run = ScanRun(kind="library", status="running")
            session.add(run)
            session.flush()
            run_id = run.id

        stats: dict[str, Any] = {"roots": []}
        status, error = "ok", None
        try:
            rules = get_rules()
            index = build_index()

            for kind, key in (("series", "series_root"), ("movie", "movies_root")):
                path = settings.get_str(key)
                result = scan_root(path, kind, rules)
                summary = summarize(result)

                if not result.is_available:
                    log.error("Корень %s недоступен: %s", path, result.error)
                    summary["error"] = result.error
                    stats["roots"].append(summary)
                    continue

                root_id = _persist_root(result)
                scope = "movie" if kind == "movie" else "series"
                summary["mapping"] = _map_folders(root_id, index, scope)
                summary["episode_links"] = _map_episodes(root_id)
                stats["roots"].append(summary)
        except Exception as exc:
            log.exception("Сканирование библиотеки упало")
            status, error = "failed", str(exc)
            raise
        finally:
            self._running = False
            with session_scope() as session:
                run = session.get(ScanRun, run_id)
                if run is not None:
                    run.status = status
                    run.finished_at = datetime.utcnow()
                    run.stats = stats
                    run.error = error

        return stats


library_scanner = LibraryScanner()
