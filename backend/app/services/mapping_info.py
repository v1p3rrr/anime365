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
    "video": "Видео",
    "sub_ru": "RU-субтитры",
    "sub_ja": "JA-субтитры",
    "sub_en": "EN-субтитры",
}


def get_title_mappings(series_id: int) -> list[dict[str, Any]]:
    """Все папки, привязанные к тайтлу, с указанием происхождения связи."""
    # Основная папка нужна загрузке: без неё при нескольких папках непонятно,
    # куда класть файл.
    ensure_primary_mapping(series_id)
    per_folder = get_per_folder_counts(series_id)

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
                    "is_primary": mapping.is_primary,
                    "primary_is_manual": mapping.primary_is_manual,
                    "needs_confirmation": mapping.needs_confirmation,
                    # На какое название .nfo сматчилось — главное для проверки глазами.
                    "nfo_title": display_title,
                    "nfo_original_title": nfo.get("original_title"),
                    "nfo_premiered": nfo.get("premiered"),
                    # Что именно лежит в этой папке — важно, когда папок несколько.
                    "counts": per_folder.get(folder.id, {}),
                }
            )

        # Основная первой: с неё начинается чтение.
        out.sort(key=lambda item: (not item["is_primary"], item["rel_path"]))
        return out


def get_local_coverage(series_id: int, total_episodes: int) -> dict[str, Any]:
    """Что из тайтла уже есть локально и чего не хватает.

    ⚠️ Считаем РАЗЛИЧНЫЕ номера серий, а не файлы. Один и тот же сезон может
    лежать в двух папках (разложенный под Jellyfin и исходный релиз в другом
    кодеке), и подсчёт по файлам давал бы «24 из 12».
    """
    with session_scope() as session:
        rows = session.execute(
            select(EpisodeMapping.role, EpisodeMapping.episode).where(
                EpisodeMapping.series_id == series_id
            )
        ).all()

        # Сколько копий у каждой пары «серия + роль» — чтобы честно сказать,
        # что часть файлов дублируется, а не молча схлопнуть их в счётчике.
        duplicate_rows = session.execute(
            select(
                EpisodeMapping.role,
                EpisodeMapping.episode,
                func.count(EpisodeMapping.id),
            )
            .where(EpisodeMapping.series_id == series_id)
            .group_by(EpisodeMapping.role, EpisodeMapping.episode)
            .having(func.count(EpisodeMapping.id) > 1)
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

    duplicates: dict[str, list[float]] = {}
    for role, episode, _count in duplicate_rows:
        duplicates.setdefault(role, []).append(float(episode))

    coverage = {}
    for role, label in ROLE_LABELS.items():
        have = by_role.get(role, set())
        missing = sorted(expected - have)
        dups = sorted(duplicates.get(role, []))
        coverage[role] = {
            "label": label,
            "have": len(have),
            "total": len(expected),
            "complete": bool(expected) and not missing,
            "missing_ranges": format_ranges(missing),
            # Серии, лежащие более чем в одной копии.
            "duplicate_count": len(dups),
            "duplicate_ranges": format_ranges(dups),
        }

    # Локально серий может оказаться больше, чем знает сайт: по ТЗ показываем локальное число.
    local_max = max((e for eps in by_role.values() for e in eps), default=0)

    return {
        "coverage": coverage,
        "episodes_expected": len(expected),
        "local_max_episode": local_max,
        "has_local_files": bool(rows),
    }


def ensure_primary_mapping(series_id: int) -> int | None:
    """Гарантирует, что у тайтла ровно одна основная папка.

    Автовыбор ранжирует по вкладу ИМЕННО в этот тайтл, а не по общему числу
    видеофайлов в папке: папка может содержать серии другого сезона, и тогда
    её размер ничего не говорит. Только при равном вкладе смотрим на число
    файлов и размер — обычно это отличает разложенную под Jellyfin папку от
    исходного релиза.

    Ручной выбор пользователя не пересматривается.
    """
    contributions = get_per_folder_counts(series_id)

    with session_scope() as session:
        rows = session.execute(
            select(TitleMapping, LibraryFolder)
            .join(LibraryFolder, TitleMapping.folder_id == LibraryFolder.id)
            .where(TitleMapping.series_id == series_id)
        ).all()

        if not rows:
            return None

        # Выбор пользователя не трогаем никогда.
        manual = [m for m, _ in rows if m.is_primary and m.primary_is_manual]
        if len(manual) == 1:
            return manual[0].id

        def rank(pair: tuple[TitleMapping, LibraryFolder]) -> tuple[int, int, int]:
            _, folder = pair
            episodes_here = sum(contributions.get(folder.id, {}).values())
            return (episodes_here, folder.video_count, folder.size_bytes)

        best = max(rows, key=rank)
        for mapping, _ in rows:
            mapping.is_primary = mapping.id == best[0].id
        return best[0].id


def set_primary_mapping(series_id: int, mapping_id: int) -> bool:
    """Ручной выбор основной папки. После него автовыбор не вмешивается."""
    with session_scope() as session:
        rows = (
            session.execute(select(TitleMapping).where(TitleMapping.series_id == series_id))
            .scalars()
            .all()
        )
        if not any(m.id == mapping_id for m in rows):
            return False
        for mapping in rows:
            mapping.is_primary = mapping.id == mapping_id
            mapping.primary_is_manual = mapping.id == mapping_id
        return True


def get_per_folder_counts(series_id: int) -> dict[int, dict[str, int]]:
    """Сколько серий каждой роли лежит в каждой папке.

    Нужно, когда папок несколько: пользователь должен видеть, что где лежит,
    а не только суммарный счётчик.
    """
    with session_scope() as session:
        rows = session.execute(
            select(
                LibraryFile.folder_id,
                EpisodeMapping.role,
                func.count(func.distinct(EpisodeMapping.episode)),
            )
            .join(LibraryFile, EpisodeMapping.file_id == LibraryFile.id)
            .where(EpisodeMapping.series_id == series_id)
            .group_by(LibraryFile.folder_id, EpisodeMapping.role)
        ).all()

    out: dict[int, dict[str, int]] = {}
    for folder_id, role, count in rows:
        out.setdefault(folder_id, {})[role] = count
    return out


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


def series_badges(series_ids: list[int] | None = None) -> dict[int, dict[str, Any]]:
    """Счётчики наличия для значков на постерах.

    `series_ids` ограничивает выборку окном карточек: гонять агрегат по всем
    29 тысячам тайтлов ради сотни видимых карточек незачем.

    Знаменатель считается по правилу из ТЗ:
    `max(numberOfEpisodes, серий-контента в API, максимальный локальный номер)`.
    `numberOfEpisodes` — это ПЛАН, а не выложенное, поэтому одного его мало:
    у Re:Zero 4th Season план 19 при 11 реальных сериях, а у давно скачанных
    тайтлов локальных серий бывает больше, чем знает сайт.
    """
    with session_scope() as session:
        role_stmt = select(
            EpisodeMapping.series_id,
            EpisodeMapping.role,
            func.count(func.distinct(EpisodeMapping.episode)),
            func.max(EpisodeMapping.episode),
        ).group_by(EpisodeMapping.series_id, EpisodeMapping.role)

        api_stmt = (
            select(Episode.series_id, func.count(func.distinct(Episode.episode_num)))
            .where(Episode.episode_type.not_in(NON_CONTENT_EPISODE_TYPES))
            .group_by(Episode.series_id)
        )
        planned_stmt = select(Series.id, Series.number_of_episodes)

        if series_ids is not None:
            if not series_ids:
                return {}
            role_stmt = role_stmt.where(EpisodeMapping.series_id.in_(series_ids))
            api_stmt = api_stmt.where(Episode.series_id.in_(series_ids))
            planned_stmt = planned_stmt.where(Series.id.in_(series_ids))

        rows = session.execute(role_stmt).all()
        api_counts = dict(session.execute(api_stmt).all())
        planned = dict(session.execute(planned_stmt).all())

    out: dict[int, dict[str, Any]] = {}
    local_max: dict[int, float] = {}
    for series_id, role, count, highest in rows:
        entry = out.setdefault(series_id, {})
        entry[role] = count
        local_max[series_id] = max(local_max.get(series_id, 0.0), float(highest or 0))

    for series_id, entry in out.items():
        entry["total"] = int(
            max(
                planned.get(series_id) or 0,
                api_counts.get(series_id) or 0,
                local_max.get(series_id, 0.0),
            )
        )
    return out
