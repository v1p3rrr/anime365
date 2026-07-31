"""Сверка нашей библиотеки с тем, что распознал Jellyfin.

Отвечает на вопрос, который иначе выясняется только листанием чужого
интерфейса глазами: **какие файлы лежат на диске, но Jellyfin их не подобрал**.
Обычно это значит, что не подошло именование — файл есть, а серии в Jellyfin
нет, и заметить это можно спустя месяцы.

Готового эндпоинта у Jellyfin для такого нет: он не сообщает, что пропустил,
он просто этого не показывает. Поэтому считаем разностью множеств на своей
стороне — наш список файлов минус его список путей.

Сравниваем по хвостам путей, как и в поиске тайтла: точки монтирования у нас и
у Jellyfin разные, структура под ними одна.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.core.db import session_scope
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot
from app.services.jellyfin import _normalize, jellyfin

log = logging.getLogger(__name__)

# Сколько имён показываем. Полный список из тысячи строк не читает никто, а
# первые сотни достаточно, чтобы увидеть закономерность — а именно она и нужна:
# нераспознанное почти всегда идёт группами по одной причине.
MAX_REPORTED = 200


def _our_video_files() -> list[dict[str, Any]]:
    """Видеофайлы, которые видит наш сканер, с путями относительно корня."""
    with session_scope() as session:
        rows = session.execute(
            select(
                LibraryFile.id,
                LibraryFile.filename,
                LibraryFile.size_bytes,
                LibraryFolder.rel_path,
                LibraryRoot.path,
                LibraryRoot.kind,
            )
            .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
            .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
            .where(LibraryFile.kind == "video", LibraryRoot.is_available.is_(True))
        ).all()

    out = []
    for row in rows:
        rel = f"{row.rel_path}/{row.filename}" if row.rel_path else row.filename
        out.append(
            {
                "id": row.id,
                "filename": row.filename,
                "rel_path": rel,
                "folder": row.rel_path,
                "root_kind": row.kind,
                "size_bytes": row.size_bytes,
            }
        )
    return out


async def audit() -> dict[str, Any]:
    """Что из нашей библиотеки Jellyfin не распознал.

    Возвращает `available: False` с причиной, когда сверять не с чем. Причины
    разведены, потому что чинятся по-разному, и потому что «0 нераспознанных»
    и «спросить не удалось» — противоположные новости, которые нельзя
    показывать одинаково.
    """
    if not jellyfin.turned_on:
        return {"available": False, "reason": "disabled"}
    if not jellyfin.configured:
        return {"available": False, "reason": "not_configured"}

    ours = _our_video_files()
    if not ours:
        return {"available": False, "reason": "library_empty"}

    theirs = await jellyfin.known_file_paths()
    if theirs is None:
        # ⚠️ Именно здесь легко было бы соврать: приняв неудачный запрос за
        # пустой ответ, отчёт объявил бы нераспознанной всю библиотеку.
        return {"available": False, "reason": "unreachable"}

    # Нормализуем и чужую сторону тоже, хотя known_file_paths() это уже делает.
    # Иначе сравнение зависит от того, кто его вызвал: регистр, пришедший от
    # Jellyfin как есть, превратился бы в ложную тревогу на весь список.
    theirs = {_normalize(path) for path in theirs}

    missing = []
    for item in ours:
        needle = _normalize(item["rel_path"])
        # Тот же принцип, что и при поиске тайтла: совпадение по границе
        # компонента пути, а не по вхождению подстроки.
        if not any(path == needle or path.endswith("/" + needle) for path in theirs):
            missing.append(item)

    missing.sort(key=lambda item: item["rel_path"])
    log.info(
        "Сверка с Jellyfin: наших файлов %d, у него %d, не распознано %d",
        len(ours),
        len(theirs),
        len(missing),
    )

    return {
        "available": True,
        "our_files": len(ours),
        "jellyfin_files": len(theirs),
        "missing_count": len(missing),
        "missing": missing[:MAX_REPORTED],
        "truncated": len(missing) > MAX_REPORTED,
        # По папкам видно закономерность: обычно не распознаётся не файл, а
        # целая папка — из-за имени, которое Jellyfin не разбирает.
        "by_folder": _group_by_folder(missing),
    }


def _group_by_folder(missing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in missing:
        counts[item["folder"] or "—"] = counts.get(item["folder"] or "—", 0) + 1
    grouped = [{"folder": folder, "count": count} for folder, count in counts.items()]
    grouped.sort(key=lambda item: (-item["count"], item["folder"]))
    return grouped[:MAX_REPORTED]
