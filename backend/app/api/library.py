"""Эндпоинты библиотеки: состояние, сканирование, нераспознанное, ручной маппинг."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.db import session_scope
from app.models.catalog import ScanRun, Series
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot, TitleMapping
from app.services.filename_rules import get_rules, parse_filename, reload_rules
from app.services.library_service import library_scanner
from app.services.library_watcher import library_watcher
from app.services.mapping_info import set_primary_mapping

log = logging.getLogger(__name__)

router = APIRouter(prefix="/library", tags=["library"])


@router.get("/status")
def library_status() -> dict[str, Any]:
    """Состояние корней и последнего сканирования."""
    with session_scope() as session:
        roots = session.execute(select(LibraryRoot)).scalars().all()
        last = session.execute(
            select(ScanRun).where(ScanRun.kind == "library").order_by(ScanRun.started_at.desc()).limit(1)
        ).scalar_one_or_none()

        mapped = session.execute(select(func.count(func.distinct(TitleMapping.folder_id)))).scalar() or 0
        total_folders = session.execute(select(func.count(LibraryFolder.id))).scalar() or 0
        needs_confirm = session.execute(
            select(func.count(TitleMapping.id)).where(TitleMapping.needs_confirmation.is_(True))
        ).scalar() or 0

        return {
            "scanning": library_scanner.running,
            # Пользователь должен знать, живёт ли библиотека на слежении или на
            # расписании: молчаливый откат — худший вариант.
            "watcher": library_watcher.refresh_status().to_dict(),
            "roots": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "path": r.path,
                    "available": r.is_available,
                    # Библиотеку штатно монтируют только на чтение — UI обязан
                    # показать это заранее, а не падать при первой загрузке.
                    "writable": r.is_writable,
                    "total_bytes": r.total_bytes,
                    "folder_count": r.folder_count,
                    "scanned_at": r.scanned_at.isoformat() if r.scanned_at else None,
                }
                for r in roots
            ],
            "folders_total": total_folders,
            "folders_mapped": mapped,
            "folders_unmapped": max(0, total_folders - mapped),
            "needs_confirmation": needs_confirm,
            "last_run": (
                {
                    "status": last.status,
                    "started_at": last.started_at.isoformat() if last.started_at else None,
                    "finished_at": last.finished_at.isoformat() if last.finished_at else None,
                    "stats": last.stats,
                    "error": last.error,
                }
                if last
                else None
            ),
        }


@router.post("/scan")
def start_scan() -> dict[str, Any]:
    if library_scanner.running:
        raise HTTPException(409, "Сканирование уже идёт")
    try:
        return library_scanner.scan_all()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/unmapped")
def unmapped_folders(
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    only_with_video: Annotated[bool, Query()] = True,
) -> list[dict[str, Any]]:
    """Папки, которым не нашлось тайтла в каталоге.

    Основной рабочий экран для ручной доводки: именно отсюда пользователь
    привязывает то, что алгоритм не осилил.
    """
    with session_scope() as session:
        mapped_ids = select(TitleMapping.folder_id)
        stmt = (
            select(LibraryFolder, LibraryRoot)
            .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
            .where(LibraryFolder.id.not_in(mapped_ids))
        )
        if only_with_video:
            stmt = stmt.where(LibraryFolder.video_count > 0)

        rows = session.execute(stmt.order_by(LibraryFolder.video_count.desc()).limit(limit)).all()

        # Названия родительских папок нужны, чтобы подсказать поиск для сезонных
        # подпапок: искать по «Season 1» бессмысленно, искать надо по тайтлу.
        parent_ids = {f.parent_id for f, _ in rows if f.parent_id}
        parents: dict[int, LibraryFolder] = {}
        if parent_ids:
            parents = {
                p.id: p
                for p in session.execute(
                    select(LibraryFolder).where(LibraryFolder.id.in_(parent_ids))
                ).scalars()
            }

        result = []
        for folder, root in rows:
            parent = parents.get(folder.parent_id) if folder.parent_id else None
            own_title = (folder.nfo or {}).get("title")
            parent_title = (parent.nfo or {}).get("title") if parent and parent.nfo else None

            # Для сезонной подпапки собственное название — это «Season 2»,
            # поэтому в подсказку идёт название родителя.
            if parent is not None:
                hint = parent_title or parent.name
            else:
                hint = own_title or folder.name

            result.append(
                {
                    "folder_id": folder.id,
                    "name": folder.name,
                    "rel_path": folder.rel_path,
                    "root_kind": root.kind,
                    "season_number": folder.season_number,
                    "video_count": folder.video_count,
                    "size_bytes": folder.size_bytes,
                    "nfo_title": own_title,
                    "nfo_year": (folder.nfo or {}).get("year"),
                    "has_nfo": bool(folder.nfo),
                    "parent_name": parent.name if parent else None,
                    "search_hint": hint,
                }
            )
        return result


@router.get("/unmatched-files")
def unmatched_files(limit: Annotated[int, Query(ge=1, le=500)] = 200) -> list[dict[str, Any]]:
    """Файлы, у которых не удалось определить номер серии."""
    with session_scope() as session:
        rows = session.execute(
            select(LibraryFile, LibraryFolder)
            .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
            .where(LibraryFile.is_unmatched.is_(True), LibraryFile.kind == "video")
            .limit(limit)
        ).all()

        return [
            {
                "file_id": file.id,
                "filename": file.filename,
                "folder": folder.rel_path,
                "size_bytes": file.size_bytes,
            }
            for file, folder in rows
        ]


@router.get("/mappings")
def list_mappings(limit: Annotated[int, Query(ge=1, le=1000)] = 500) -> list[dict[str, Any]]:
    """Все связи папка-тайтл с указанием, чем именно они получены."""
    with session_scope() as session:
        rows = session.execute(
            select(TitleMapping, LibraryFolder, Series)
            .join(LibraryFolder, TitleMapping.folder_id == LibraryFolder.id)
            .join(Series, TitleMapping.series_id == Series.id)
            .order_by(TitleMapping.confidence)
            .limit(limit)
        ).all()

        return [
            {
                "id": mapping.id,
                "folder_id": folder.id,
                "folder": folder.rel_path,
                "season_number": folder.season_number,
                "video_count": folder.video_count,
                "series_id": series.id,
                "series_title": (series.titles or {}).get("romaji") or series.title,
                "series_year": series.year,
                "series_season": series.season,
                "source": mapping.source,
                "confidence": mapping.confidence,
                "matched_on": mapping.matched_on,
                "is_manual": mapping.is_manual,
                "needs_confirmation": mapping.needs_confirmation,
            }
            for mapping, folder, series in rows
        ]


class ManualMapping(BaseModel):
    folder_id: int
    series_id: int


@router.post("/mappings")
def create_mapping(payload: ManualMapping) -> dict[str, Any]:
    """Ручная привязка. Помечается is_manual и не перетирается сканированием."""
    with session_scope() as session:
        folder = session.get(LibraryFolder, payload.folder_id)
        if folder is None:
            raise HTTPException(404, "Папка не найдена")
        if session.get(Series, payload.series_id) is None:
            raise HTTPException(404, "Тайтл не найден")

        existing = session.execute(
            select(TitleMapping).where(
                TitleMapping.folder_id == payload.folder_id,
                TitleMapping.series_id == payload.series_id,
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.is_manual = True
            existing.needs_confirmation = False
            existing.confidence = 100.0
            existing.source = "manual"
            return {"id": existing.id, "updated": True}

        mapping = TitleMapping(
            series_id=payload.series_id,
            folder_id=payload.folder_id,
            source="manual",
            confidence=100.0,
            matched_on="указано вручную",
            is_manual=True,
            needs_confirmation=False,
        )
        session.add(mapping)
        session.flush()
        return {"id": mapping.id, "updated": False}


class PrimaryRequest(BaseModel):
    series_id: int


@router.post("/mappings/{mapping_id}/primary")
def make_primary(mapping_id: int, payload: PrimaryRequest) -> dict[str, Any]:
    """Назначает папку основной — туда будут складываться загрузки.

    Автоматически основной становится папка с наибольшим числом видео, но выбор
    можно переопределить: у одного сезона бывают две копии в разных кодеках, и
    какая из них «рабочая» — решает пользователь.
    """
    if not set_primary_mapping(payload.series_id, mapping_id):
        raise HTTPException(404, "Связь не найдена у этого тайтла")
    return {"id": mapping_id, "is_primary": True}


@router.post("/mappings/{mapping_id}/confirm")
def confirm_mapping(mapping_id: int) -> dict[str, Any]:
    """Подтверждение нечёткого совпадения — оно становится ручным."""
    with session_scope() as session:
        mapping = session.get(TitleMapping, mapping_id)
        if mapping is None:
            raise HTTPException(404, "Связь не найдена")
        mapping.needs_confirmation = False
        mapping.is_manual = True
        return {"id": mapping_id, "confirmed": True}


@router.delete("/mappings/{mapping_id}")
def delete_mapping(mapping_id: int) -> dict[str, Any]:
    with session_scope() as session:
        mapping = session.get(TitleMapping, mapping_id)
        if mapping is None:
            raise HTTPException(404, "Связь не найдена")
        session.delete(mapping)
    return {"deleted": mapping_id}


@router.post("/parse-test")
def parse_test(
    names: Annotated[list[str], Body(embed=True)],
    scope: Annotated[str, Body(embed=True)] = "series",
) -> dict[str, Any]:
    """Пробный разбор имён без сканирования.

    Рабочий цикл настройки правил: сюда скармливается список файлов, в ответе
    видно, что каким правилом разобралось и что осталось нераспознанным.
    """
    if len(names) > 5000:
        raise HTTPException(400, "За раз не более 5000 имён")

    rules = get_rules()
    parsed: list[dict[str, Any]] = []
    by_rule: dict[str, int] = {}
    unparsed: list[str] = []

    for name in names:
        result = parse_filename(name, scope, rules)
        parsed.append(result.to_dict())
        if result.excluded_by:
            by_rule["(исключён)"] = by_rule.get("(исключён)", 0) + 1
        elif result.is_parsed:
            key = result.matched_rule_id or "(без правила)"
            by_rule[key] = by_rule.get(key, 0) + 1
        else:
            by_rule["(не разобрано)"] = by_rule.get("(не разобрано)", 0) + 1
            unparsed.append(name)

    return {
        "total": len(names),
        "by_rule": by_rule,
        "unparsed": unparsed[:100],
        "results": parsed[:500],
    }


@router.post("/reload-rules")
def reload_parsing_rules() -> dict[str, Any]:
    """Перечитывает config/filename_rules.yaml без перезапуска сервиса."""
    rules = reload_rules()
    return {"rules": len(rules.rules), "exclude_patterns": len(rules.exclude)}
