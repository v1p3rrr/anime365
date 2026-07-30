"""Сведения о привязке тайтла к локальным папкам — для плашки на странице тайтла.

Пользователь должен видеть не только «смаплено», но и ЧЕМ именно смаплено и на
какое название: иначе ошибочная привязка обнаруживается только после того, как
файлы уедут не в ту папку.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.core.db import session_scope
from app.models.catalog import Episode, Series
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, LibraryRoot, TitleMapping
from app.services.coverage import NON_CONTENT_EPISODE_TYPES, format_ranges

# Какие роли считаем за «есть локально» при подсчёте покрытия.
ROLE_LABELS = {
    "video": "видео",
    "sub_ru": "RU-субтитры",
    "sub_ja": "JA-субтитры",
    "sub_en": "EN-субтитры",
}


def get_title_mappings(series_id: int) -> list[dict[str, Any]]:
    """Все папки, привязанные к тайтлу, с указанием происхождения связи."""
    with session_scope() as session:
        rows = session.execute(
            select(TitleMapping, LibraryFolder, LibraryRoot)
            .join(LibraryFolder, TitleMapping.folder_id == LibraryFolder.id)
            .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
            .where(TitleMapping.series_id == series_id)
        ).all()

        out = []
        for mapping, folder, root in rows:
            nfo = folder.nfo or {}
            own_title = nfo.get("title")

            # У сезонной подпапки собственное название — это «Season 2», для
            # проверки привязки глазами оно бесполезно. Показываем название
            # родительского тайтла, а номер сезона и так выведен отдельно.
            parent_title = None
            if folder.parent_id:
                parent = session.get(LibraryFolder, folder.parent_id)
                if parent is not None:
                    parent_title = (parent.nfo or {}).get("title") or parent.name
            display_title = parent_title or own_title

            out.append(
                {
                    "mapping_id": mapping.id,
                    "folder_id": folder.id,
                    "rel_path": folder.rel_path,
                    "root_path": root.path,
                    "root_kind": root.kind,
                    # Загрузки в папку на томе только для чтения невозможны.
                    "writable": root.is_writable,
                    "season_number": folder.season_number,
                    "video_count": folder.video_count,
                    "size_bytes": folder.size_bytes,
                    "source": mapping.source,
                    "confidence": mapping.confidence,
                    "matched_on": mapping.matched_on,
                    "is_manual": mapping.is_manual,
                    "needs_confirmation": mapping.needs_confirmation,
                    # На какое название .nfo сматчилось — главное для проверки глазами.
                    "nfo_title": display_title,
                    "nfo_original_title": nfo.get("original_title"),
                    "nfo_premiered": nfo.get("premiered"),
                }
            )
        return out


def get_local_coverage(series_id: int, total_episodes: int) -> dict[str, Any]:
    """Что из тайтла уже есть локально и чего не хватает."""
    with session_scope() as session:
        rows = session.execute(
            select(EpisodeMapping.role, EpisodeMapping.episode).where(
                EpisodeMapping.series_id == series_id
            )
        ).all()

        # Номера серий, известные API, — чтобы не считать недостающими то,
        # чего на сайте и нет.
        api_episodes = {
            float(e.episode_num)
            for e in session.execute(
                select(Episode).where(Episode.series_id == series_id)
            ).scalars()
            if e.episode_num is not None and e.episode_type not in NON_CONTENT_EPISODE_TYPES
        }

    by_role: dict[str, set[float]] = {}
    for role, episode in rows:
        by_role.setdefault(role, set()).add(float(episode))

    expected = api_episodes or {float(i) for i in range(1, (total_episodes or 0) + 1)}

    coverage = {}
    for role, label in ROLE_LABELS.items():
        have = by_role.get(role, set())
        missing = sorted(expected - have)
        coverage[role] = {
            "label": label,
            "have": len(have),
            "total": len(expected),
            "complete": bool(expected) and not missing,
            "missing_ranges": format_ranges(missing),
        }

    # Локально серий может оказаться больше, чем знает сайт: по ТЗ показываем локальное число.
    local_max = max((e for eps in by_role.values() for e in eps), default=0)

    return {
        "coverage": coverage,
        "episodes_expected": len(expected),
        "local_max_episode": local_max,
        "has_local_files": bool(rows),
    }


def get_local_files(series_id: int) -> list[dict[str, Any]]:
    """Локальные файлы тайтла по сериям — для списка эпизодов."""
    with session_scope() as session:
        rows = session.execute(
            select(EpisodeMapping, LibraryFile, LibraryFolder)
            .join(LibraryFile, EpisodeMapping.file_id == LibraryFile.id)
            .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
            .where(EpisodeMapping.series_id == series_id)
        ).all()

        return [
            {
                "episode": float(mapping.episode),
                "role": mapping.role,
                "filename": file.filename,
                "folder": folder.rel_path,
                "size_bytes": file.size_bytes,
                "lang": file.lang,
                "translation_id": file.anime365_translation_id,
                "streams": file.streams,
            }
            for mapping, file, folder in rows
        ]


def count_mapped_series() -> int:
    with session_scope() as session:
        return session.execute(
            select(func.count(func.distinct(TitleMapping.series_id)))
        ).scalar() or 0


def mapped_series_ids() -> set[int]:
    """Тайтлы, у которых есть хоть одна привязанная папка — для фильтра каталога."""
    with session_scope() as session:
        return {
            row[0]
            for row in session.execute(select(func.distinct(TitleMapping.series_id))).all()
        }


def series_badges() -> dict[int, dict[str, Any]]:
    """Счётчики наличия по всем смапленным тайтлам — для значков на постерах."""
    with session_scope() as session:
        rows = session.execute(
            select(
                EpisodeMapping.series_id,
                EpisodeMapping.role,
                func.count(func.distinct(EpisodeMapping.episode)),
            ).group_by(EpisodeMapping.series_id, EpisodeMapping.role)
        ).all()

        totals = {
            sid: total
            for sid, total in session.execute(
                select(Series.id, Series.number_of_episodes)
            ).all()
        }

    out: dict[int, dict[str, Any]] = {}
    for series_id, role, count in rows:
        entry = out.setdefault(series_id, {"total": totals.get(series_id) or 0})
        entry[role] = count
    return out
