"""Сохранение результатов сканирования и автоматический маппинг."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
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
from app.services.library_scan import (
    FolderState,
    RootScanResult,
    ScannedFolder,
    scan_root,
    summarize,
)
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

    existing.parent_id = parent_id
    existing.scanned_at = datetime.utcnow()

    if folder.unchanged:
        # Папка не менялась — её файлы уже лежат в БД разобранными. Перезаписать
        # их пустым списком означало бы стереть библиотеку, поэтому выходим
        # сразу, обойдя только детей: внутри них изменения возможны.
        session.flush()
        for child in folder.children:
            _persist_folder(session, root_id, child, parent_id=existing.id)
        return existing

    existing.name = folder.name
    existing.season_number = folder.season_number
    existing.nfo = folder.nfo.to_dict() if folder.nfo else None
    existing.size_bytes = folder.size_bytes
    existing.video_count = folder.video_count
    existing.mtime = folder.mtime
    session.flush()

    # ⚠️ Файлы обновляются НА МЕСТЕ, по имени, а не пересоздаются.
    #
    # Раньше здесь был `delete(...)` + вставка заново. Это выглядело безобидно,
    # но `episode_mapping.file_id` объявлен с ondelete=CASCADE, поэтому удаление
    # строк файла молча уносило все связи серий с файлами — включая ручные.
    # Проверка `is_manual` в _map_episodes при этом ничего не спасала: она
    # работала уже по пустой таблице. То есть ручная привязка серии не
    # переживала ни одного сканирования.
    #
    # Стабильный file_id нужен всему, что ссылается на файл, поэтому строки
    # переиспользуются, а исчезнувшие удаляются точечно.
    existing_files = {
        f.filename: f
        for f in session.execute(
            select(LibraryFile).where(LibraryFile.folder_id == existing.id)
        ).scalars()
    }
    seen: set[str] = set()

    for file in folder.files:
        seen.add(file.filename)
        row = existing_files.get(file.filename)
        if row is None:
            row = LibraryFile(folder_id=existing.id, filename=file.filename)
            session.add(row)

        row.kind = file.kind
        row.lang = file.lang
        row.season_number = file.season_number
        row.episode = file.episode
        row.episode_end = file.episode_end
        row.matched_rule_id = file.matched_rule_id
        row.release_group = file.release_group
        row.anime365_translation_id = file.anime365_translation_id
        row.streams = file.streams
        row.size_bytes = file.size_bytes
        row.mtime = file.mtime
        row.is_unmatched = file.is_unmatched
        row.excluded_reason = file.excluded_reason

    vanished = [row.id for name, row in existing_files.items() if name not in seen]
    if vanished:
        # Файла больше нет на диске — вот здесь каскад как раз уместен.
        session.execute(delete(LibraryFile).where(LibraryFile.id.in_(vanished)))

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
        root.folder_count = len(result.folders)
        root.scanned_at = datetime.utcnow()
        session.flush()
        root_id = root.id

        for folder in result.folders:
            _persist_folder(session, root_id, folder)

        # Размер считаем по БД, а не по результату прохода: при инкрементальном
        # скане у неизменившихся папок size_bytes в результате нулевой, и корень
        # «похудел» бы до размера только тех папок, что реально пересканированы.
        session.flush()
        root.total_bytes = session.execute(
            select(func.coalesce(func.sum(LibraryFolder.size_bytes), 0)).where(
                LibraryFolder.root_id == root_id
            )
        ).scalar_one()

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
            file_ids = [f.id for f in files]
            if not file_ids:
                continue

            session.execute(
                delete(EpisodeMapping).where(
                    EpisodeMapping.series_id == mapping.series_id,
                    EpisodeMapping.is_manual.is_(False),
                    EpisodeMapping.file_id.in_(file_ids),
                )
            )

            # Файлы, которые пользователь привязал руками. Автоматическую связь
            # им ставить нельзя: она не заменит ручную, а ляжет рядом, и файл
            # начнёт засчитываться сразу за две серии.
            manual_files = {
                row[0]
                for row in session.execute(
                    select(EpisodeMapping.file_id).where(
                        EpisodeMapping.series_id == mapping.series_id,
                        EpisodeMapping.is_manual.is_(True),
                        EpisodeMapping.file_id.in_(file_ids),
                    )
                ).all()
            }

            for file in files:
                if file.episode is None or file.excluded_reason:
                    continue
                if file.id in manual_files:
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

    def scan_all(self, full: bool = False) -> dict[str, Any]:
        """Сканирует оба корня.

        `full=True` заставляет разобрать каждую папку заново. Инкрементальный
        режим не видит замену файла на месте под тем же именем — mtime папки от
        этого не меняется, — поэтому сверочный проход по расписанию идёт полным.
        """
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
                known = None if full else _known_states(path)
                result = scan_root(path, kind, rules, known=known)

                if not result.is_available:
                    log.error("Корень %s недоступен: %s", path, result.error)
                    summary = summarize(result)
                    summary["error"] = result.error
                    stats["roots"].append(summary)
                    continue

                root_id = _persist_root(result)
                # Сводку берём из БД: при инкременте разобранных файлов в
                # результате прохода почти нет, а показывать надо всю библиотеку.
                summary = summarize_from_db(root_id, result)
                scope = "movie" if kind == "movie" else "series"
                summary["mapping"] = _map_folders(root_id, index, scope)
                summary["episode_links"] = _map_episodes(root_id)
                stats["roots"].append(summary)

            stats["mode"] = "full" if full else "incremental"
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


def load_folder_states(root_id: int) -> dict[str, FolderState]:
    """Слепки папок из прошлого прохода — вход для инкрементального скана."""
    with session_scope() as session:
        rows = session.execute(
            select(
                LibraryFolder.rel_path,
                LibraryFolder.mtime,
                func.count(LibraryFile.id),
            )
            .join(LibraryFile, LibraryFile.folder_id == LibraryFolder.id, isouter=True)
            .where(LibraryFolder.root_id == root_id)
            .group_by(LibraryFolder.id)
        ).all()
    return {rel: FolderState(mtime=mtime, file_count=count) for rel, mtime, count in rows}


def summarize_from_db(root_id: int, result: RootScanResult) -> dict[str, Any]:
    """Сводка по содержимому БД, а не по результату прохода.

    При инкрементальном скане неизменившиеся папки не разбираются, и их файлов
    в результате прохода нет вовсе. Считать сводку по нему означало бы показать
    «видео: 12» вместо «видео: 2284» сразу после того, как ничего не менялось.
    БД же содержит полную картину независимо от режима.
    """
    with session_scope() as session:
        folder_ids = select(LibraryFolder.id).where(LibraryFolder.root_id == root_id)

        videos, unmatched, excluded, subs = session.execute(
            select(
                func.count().filter(
                    LibraryFile.kind == "video", LibraryFile.excluded_reason.is_(None)
                ),
                func.count().filter(
                    LibraryFile.kind == "video",
                    LibraryFile.excluded_reason.is_(None),
                    LibraryFile.is_unmatched.is_(True),
                ),
                func.count().filter(LibraryFile.excluded_reason.is_not(None)),
                func.count().filter(
                    LibraryFile.kind == "subtitle", LibraryFile.excluded_reason.is_(None)
                ),
            ).where(LibraryFile.folder_id.in_(folder_ids))
        ).one()

        lang_rows = session.execute(
            select(LibraryFile.lang, func.count())
            .where(
                LibraryFile.folder_id.in_(folder_ids),
                LibraryFile.kind == "subtitle",
                LibraryFile.excluded_reason.is_(None),
            )
            .group_by(LibraryFile.lang)
        ).all()

        root = session.get(LibraryRoot, root_id)
        total_bytes = root.total_bytes if root is not None else 0
        title_folders = session.execute(
            select(func.count())
            .select_from(LibraryFolder)
            .where(LibraryFolder.root_id == root_id, LibraryFolder.parent_id.is_(None))
        ).scalar_one()

    return {
        "path": result.path,
        "kind": result.kind,
        "available": result.is_available,
        "writable": result.is_writable,
        "title_folders": title_folders,
        "videos": videos,
        "unmatched_videos": unmatched,
        "excluded_files": excluded,
        "subtitles": subs,
        "subtitle_languages": {lang or "unknown": count for lang, count in lang_rows},
        "total_bytes": total_bytes,
        "rescanned_folders": _count_rescanned(result),
        "scanned_at": datetime.utcnow().isoformat(),
    }


def _count_rescanned(result: RootScanResult) -> int:
    """Сколько папок пришлось разобрать заново — видно, работает ли инкремент."""

    def walk(folder: ScannedFolder) -> int:
        return (0 if folder.unchanged else 1) + sum(walk(c) for c in folder.children)

    return sum(walk(f) for f in result.folders)


def _known_states(root_path: str) -> dict[str, FolderState] | None:
    """Слепки папок корня, если он уже сканировался. Иначе None — полный проход."""
    with session_scope() as session:
        root = session.execute(
            select(LibraryRoot).where(LibraryRoot.path == root_path)
        ).scalar_one_or_none()
        if root is None:
            return None
        root_id = root.id
    return load_folder_states(root_id)
