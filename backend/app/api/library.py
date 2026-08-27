"""Эндпоинты библиотеки: состояние, сканирование, нераспознанное, ручной маппинг."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.deps import rate_limit
from app.core import errors
from app.core.errors import ApiError
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.core.db import session_scope
from app.core.permissions import Permission, require
from app.services.stream_probe import stream_probe
from app.models.catalog import ScanRun, Series
from app.models.library import (
    EpisodeMapping,
    EpisodeTarget,
    LibraryFile,
    LibraryFolder,
    LibraryRoot,
    TitleMapping,
)
from app.services.filename_rules import get_rules, parse_filename, reload_rules
from app.services.library_service import SUB_ROLE, library_scanner
from app.services.library_watcher import library_watcher
from app.services.mapping_info import set_primary_mapping

log = logging.getLogger(__name__)

router = APIRouter(prefix="/library", tags=["library"])

#: Сколько находок отдавать при поиске по всей библиотеке.
#:
#: Папок тысячи, файлов десятки тысяч. Ограничение здесь не про скорость запроса,
#: а про смысл выдачи: список на две тысячи строк выбирать не легче, чем искать
#: руками, а уточнить запрос дешевле.
FOLDER_SEARCH_LIMIT = 60
FILE_SEARCH_LIMIT = 100


def _disk_usage(path: Path) -> dict[str, Any]:
    """Место на носителе, где лежит корень: сколько всего, сколько свободно.

    Вопрос «как это узнать, если папки монтируются по отдельности» решается сам
    собой: `disk_usage` спрашивает файловую систему по конкретному пути, и
    каждое монтирование отвечает за себя. Ходить в `/proc/mounts` не нужно.

    Зато нужен `mount`: два корня могут оказаться на одном носителе, и тогда их
    свободное место — **одно и то же число**, а не два независимых. Сложить их
    значило бы показать вдвое больше места, чем есть. Ключ монтирования отдаём
    клиенту, чтобы он показывал такие корни одной строкой.

    Недоступный корень (не примонтирован, снят диск) не должен ронять весь
    ответ: статус библиотеки нужен как раз тогда, когда что-то отвалилось.
    """
    import shutil

    from app.services.downloader import _mount_id

    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {"disk_total_bytes": None, "disk_free_bytes": None, "mount": None}

    # Вне Linux mountinfo нет; там ключом остаётся сам носитель.
    mount = _mount_id(path)
    if mount is None:
        try:
            mount = str(path.stat().st_dev)
        except OSError:
            mount = None

    return {
        "disk_total_bytes": usage.total,
        "disk_free_bytes": usage.free,
        "mount": mount,
    }


def _container_folder_ids():
    """Папки-контейнеры: своих видео нет, а подпапку держит тот же скан.

    Вынесено в одно место намеренно. Правило появилось в списке нераспознанного,
    а счётчики в `/status` остались считать по-старому — и статистика говорила
    «без привязки: 1» при пустом разделе. Расхождение заметил пользователь.
    """
    mapped_ids = select(TitleMapping.folder_id)
    parents_of_mapped = select(LibraryFolder.parent_id).where(
        LibraryFolder.parent_id.is_not(None),
        LibraryFolder.id.in_(mapped_ids),
    )
    return select(LibraryFolder.id).where(
        LibraryFolder.id.not_in(mapped_ids),
        LibraryFolder.id.in_(parents_of_mapped),
        LibraryFolder.video_count == 0,
    )


@router.get(
    "/status",
    dependencies=[Depends(require(Permission.CATALOG))],
)
def library_status() -> dict[str, Any]:
    """Состояние корней и последнего сканирования."""
    with session_scope() as session:
        roots = session.execute(select(LibraryRoot)).scalars().all()
        last = session.execute(
            select(ScanRun).where(ScanRun.kind == "library").order_by(ScanRun.started_at.desc()).limit(1)
        ).scalar_one_or_none()

        mapped = session.execute(select(func.count(func.distinct(TitleMapping.folder_id)))).scalar() or 0
        # Контейнеры из общего числа исключаем: они не «ждут привязки», привязку
        # с них снимает сам скан. Считать их непривязанными значит показывать
        # долг, которого нет, и звать чинить то, что в порядке.
        containers = session.execute(
            select(func.count()).select_from(_container_folder_ids().subquery())
        ).scalar() or 0
        total_folders = (
            session.execute(select(func.count(LibraryFolder.id))).scalar() or 0
        ) - containers
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
                    **_disk_usage(Path(r.path)),
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


@router.post(
    "/scan",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def start_scan(
    full: Annotated[
        bool, Query(description="Разобрать все папки заново, не полагаясь на mtime")
    ] = False,
) -> dict[str, Any]:
    if library_scanner.running:
        raise ApiError(409, errors.SCAN_RUNNING, "Сканирование уже идёт")
    try:
        return library_scanner.scan_all(full=full)
    except RuntimeError as exc:
        raise ApiError(409, errors.SCAN_REJECTED, str(exc), reason=str(exc)) from exc


@router.get(
    "/unmapped",
    dependencies=[Depends(require(Permission.CATALOG))],
)
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
        # Папка-контейнер не «нераспознана»: привязку с неё снимает сам скан,
        # когда тот же тайтл держит подпапка с файлами (см. D-62). Правило одно
        # на список и на счётчики — см. `_container_folder_ids`.
        stmt = (
            select(LibraryFolder, LibraryRoot)
            .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
            .where(
                LibraryFolder.id.not_in(mapped_ids),
                LibraryFolder.id.not_in(_container_folder_ids()),
            )
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


@router.get(
    "/folders",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def search_folders(
    query: Annotated[str, Query(max_length=200, description="Часть пути папки")] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
) -> list[dict[str, Any]]:
    """Поиск по **всем** папкам библиотеки, а не только по нераспознанным.

    ⚠️ Заведено по жалобе: «не даёт выбрать папку, в выборке несколько
    случайных, поиск результатов не даёт». Причина была в том, что окно
    привязки тайтла к папке спрашивало список нераспознанного, а нужная папка
    обычно **уже** к чему-то привязана и в него не попадала вовсе. Фильтр при
    этом работал на клиенте по загруженному куску, поэтому и поиск молчал.

    Отбор идёт на сервере: папок тысячи, и отдавать их целиком ради подстроки
    незачем. Пустой запрос даёт первые по числу видео — так в списке сразу
    видны настоящие папки тайтлов, а не служебная мелочь.
    """
    with session_scope() as session:
        stmt = select(LibraryFolder, LibraryRoot).join(
            LibraryRoot, LibraryFolder.root_id == LibraryRoot.id
        )
        needle = query.strip()
        if needle:
            # `ilike` в SQLite регистронезависим только для латиницы, поэтому
            # кириллицу приводим сами — так «зомби» находит «Зомбилэнд».
            pattern = f"%{needle.lower()}%"
            stmt = stmt.where(func.lower(LibraryFolder.rel_path).like(pattern))

        rows = session.execute(
            stmt.order_by(LibraryFolder.video_count.desc(), LibraryFolder.rel_path).limit(limit)
        ).all()

        return [
            {
                "folder_id": folder.id,
                "name": folder.name,
                "rel_path": folder.rel_path,
                "root_kind": root.kind,
                "season_number": folder.season_number,
                "video_count": folder.video_count,
                "size_bytes": folder.size_bytes,
                "nfo_title": (folder.nfo or {}).get("title"),
                "nfo_year": (folder.nfo or {}).get("year"),
                "has_nfo": bool(folder.nfo),
                "parent_name": None,
                "search_hint": (folder.nfo or {}).get("title") or folder.name,
            }
            for folder, root in rows
        ]


@router.get(
    "/unmatched-files",
    dependencies=[Depends(require(Permission.CATALOG))],
)
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


@router.get(
    "/jellyfin-audit",
    # Обходит библиотеку Jellyfin целиком, поэтому не чаще, чем нужно человеку.
    # Право `library`, а не `catalog`: метод хоть и читающий, но это не просмотр,
    # а обслуживание — тяжёлый обход чужого сервера по нажатию кнопки, и отвечает
    # он на вопрос того, кто эту библиотеку ведёт.
    dependencies=[
        Depends(require(Permission.LIBRARY)),
        Depends(rate_limit("jellyfin_audit", limit=10, window=60)),
    ],
)
async def jellyfin_audit() -> dict[str, Any]:
    """Какие наши файлы Jellyfin не распознал.

    Готового ответа у Jellyfin нет: он не сообщает, что пропустил, — он просто
    этого не показывает. Считается разностью множеств на нашей стороне.
    """
    from app.services.jellyfin_audit import audit

    return await audit()


#: Как идентификаторы Jellyfin ложатся на наши поля каталога.
PROVIDER_FIELDS = {
    "anilist": "anilist_id",
    "shikimori": "mal_id",
    "myanimelist": "mal_id",
    "mal": "mal_id",
}

#: Чьё расхождение считается расхождением.
#:
#: Только AniList. Это единственное сравнение «яблок с яблоками»: у нас есть
#: `anilist_id`, у Jellyfin — `AniList`, нумерация одна.
#:
#: Shikimori в счёт не идёт, хотя показывается. Замерено на живой библиотеке
#: (200 элементов): `Shikimori` стоит у 197 из них, но его нумерация совпадает с
#: MyAnimeList не всегда — у `[Oshi no Ko]` это 39972 против AniList 150672, у
#: `Amagami SS` оба по 8676. Плюс ещё при разборе `.nfo` выяснилось, что тег
#: `<shikimoriid>` **бывает просто неверным** (у YuruYuri он указывал на другое
#: аниме). Считать такие расхождения спором значит показывать значок почти
#: везде — а подсказка, которая горит всегда, не подсказывает ничего.
CONFLICT_PROVIDERS = frozenset({"anilist"})


def _attach_titles(rows: list[dict[str, Any]]) -> None:
    """Дописывает названия тайтлов к расходящимся строкам.

    ⚠️ Ради этого всё и делается. «anilist 21613 против 135865» человек не
    читает — числа ничего не говорят. «Youjo Senki против Youjo Senki II»
    читается мгновенно: сразу видно, что Jellyfin показывает на первый сезон,
    а мы на второй.

    Обе стороны ищутся **в нашем же каталоге**: идентификатор чужой, но
    нумерация общая — `anilist_id` у нас и `AniList` у Jellyfin это одно и то
    же пространство. Поэтому чужое число тоже разрешается в название, и
    никакого обращения наружу для этого не нужно.

    Не нашлось — поля просто не будет: у Anime365 есть не всякий тайтл, и
    выдумывать подпись вместо этого нечем.
    """
    wanted: dict[str, set[int]] = {}
    for row in rows:
        if not row.get("values_differ"):
            # У совпавших строк подписывать нечего: название было бы одно и то
            # же дважды, то есть тот самый шум, от которого уходим.
            continue
        field = PROVIDER_FIELDS[row["provider"]]
        for raw in (row["theirs"], row["ours"]):
            try:
                wanted.setdefault(field, set()).add(int(raw))
            except (TypeError, ValueError):
                continue

    if not wanted:
        return

    found: dict[tuple[str, int], str] = {}
    with session_scope() as session:
        for field, ids in wanted.items():
            column = getattr(Series, field)
            for value, title, titles_json in session.execute(
                select(column, Series.title, Series.titles).where(column.in_(sorted(ids)))
            ).all():
                # Romaji, а не склейка «ru / romaji» из `title`: в подсказке
                # строка короткая, и длинное двойное название её разорвёт.
                name = (titles_json or {}).get("romaji") or title
                if name:
                    found[(field, int(value))] = name

    for row in rows:
        if not row.get("values_differ"):
            continue
        field = PROVIDER_FIELDS[row["provider"]]
        for side in ("theirs", "ours"):
            try:
                key = (field, int(row[side]))
            except (TypeError, ValueError):
                continue
            if key in found:
                row[f"{side}_title"] = found[key]


@router.get(
    "/jellyfin/verdict/{series_id}",
    dependencies=[Depends(require(Permission.CATALOG))],
)
async def jellyfin_verdict(series_id: int) -> dict[str, Any]:
    """Что о папке этого тайтла думает Jellyfin — для подсказки, не для решения.

    ⚠️ Это **информация, а не голос**. Решение пользователя: разметку не
    переголосовывать, а показывать значок с подсказкой при наведении. Основание
    простое — исправлять чужую ошибку идентификации не наша задача, а вот
    молчать о расхождении нельзя: пока файлы не уехали не туда, его не видно.

    Почему отдельным запросом, а не в составе страницы тайтла: Jellyfin бывает
    выключен, недоступен или медленен, и страница не должна его ждать.
    """
    from app.services.jellyfin import jellyfin
    from app.services.mapping_info import title_folder_paths, title_root_kind

    if not jellyfin.turned_on or not jellyfin.configured:
        return {"available": False, "reason": "disabled"}

    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            raise ApiError(404, errors.SERIES_NOT_FOUND, "Тайтл не найден")
        ours = {"anilist_id": series.anilist_id, "mal_id": series.mal_id}

    rel_paths = title_folder_paths(series_id)
    if not rel_paths:
        return {"available": False, "reason": "not_mapped"}

    item = await jellyfin.find_item(rel_paths, title_root_kind(series_id))
    if item is None:
        return {"available": False, "reason": "not_found"}

    providers: dict[str, str] = item.get("providers") or {}
    conflicts: list[dict[str, Any]] = []
    for name, value in sorted(providers.items()):
        field = PROVIDER_FIELDS.get(name)
        our_value = ours.get(field) if field else None
        if our_value is None:
            # ⚠️ Сравнивать нечем — не показываем вовсе. Про TVDB, TMDB, AniDB и
            # Kitsu в каталоге Anime365 полей нет, и раньше такие строки шли с
            # прочерком «чтобы человек опознал тайтл». На деле опознавать по
            # голому числу нечего, а половина таблицы уходила под прочерки.
            # Опознание теперь даёт название (см. ниже), и шум стал не нужен.
            continue
        conflicts.append(
            {
                "provider": name,
                "theirs": value,
                "ours": our_value,
                "differs": name in CONFLICT_PROVIDERS and str(our_value) != str(value),
                # Отдельно от `differs`: у Shikimori расхождение спором не
                # считается ([D-98](../../../claude/09-decisions.md)), но
                # названия там показать так же полезно.
                "values_differ": str(our_value) != str(value),
            }
        )

    _attach_titles(conflicts)

    return {
        "available": True,
        "item_id": item["id"],
        "path": item["path"],
        "ids": conflicts,
        # Отдельным полем, чтобы интерфейс не пересчитывал это сам и не разошёлся
        # с сервером в трактовке.
        "disagrees": any(row["differs"] for row in conflicts),
    }


@router.get(
    "/audio",
    dependencies=[Depends(require(Permission.CATALOG))],
)
def audio_library() -> list[dict[str, Any]]:
    """Вся фонотека, сгруппированная по тайтлам."""
    from app.services.audio_library import library

    return library()


@router.get(
    "/jellyfin/collections",
    # Читающий метод, но право то же, что у добавления в коллекцию: список нужен
    # только форме добавления, и отдавать его тому, кому добавлять нельзя, — это
    # показывать устройство чужой библиотеки без всякой пользы.
    dependencies=[Depends(require(Permission.LIBRARY))],
)
async def list_jellyfin_collections(
    series_id: Annotated[int | None, Query(description="Отметить те, где тайтл уже состоит")] = None,
) -> dict[str, Any]:
    """Коллекции, которые уже есть в Jellyfin.

    Нужны, чтобы добавлять в существующую, а не плодить одноимённые: у Jellyfin
    нет запрета на две коллекции с одним названием, и разъехались бы они молча.

    С `series_id` дополнительно помечаем те, где тайтл уже лежит — иначе человек
    добавляет его во второй раз, не зная об этом. Состав приходится спрашивать
    по коллекции: `Ancestors` у элемента коллекцию не показывает (проверено),
    но коллекций единицы, так что обход дешёвый.
    """
    from app.services.jellyfin import jellyfin

    if not jellyfin.turned_on or not jellyfin.configured:
        return {"available": False, "collections": []}

    rows = await jellyfin.collections()
    if rows is None:
        return {"available": False, "collections": [], "unreachable": True}

    if series_id is not None:
        found, _ = await _resolve_items([series_id])
        item_id = found[0] if found else None
        if item_id:
            for row in rows:
                row["contains"] = item_id in await jellyfin.collection_members(row["id"])

    return {"available": True, "collections": rows}


async def _resolve_items(series_ids: list[int]) -> tuple[list[str], list[int]]:
    """Тайтлы -> элементы Jellyfin. Второй список — кого не нашли."""
    from app.services.jellyfin import jellyfin
    from app.services.mapping_info import title_folder_paths, title_root_kind

    found: list[str] = []
    missing: list[int] = []
    for series_id in series_ids:
        rel_paths = title_folder_paths(series_id)
        item = (
            await jellyfin.find_item(rel_paths, title_root_kind(series_id))
            if rel_paths
            else None
        )
        if item:
            found.append(item["id"])
        else:
            missing.append(series_id)
    return found, missing


def _require_jellyfin() -> None:
    from app.services.jellyfin import jellyfin

    if not jellyfin.turned_on:
        raise ApiError(409, errors.JELLYFIN_DISABLED, "Интеграция с Jellyfin выключена")
    if not jellyfin.configured:
        raise ApiError(409, errors.JELLYFIN_DISABLED, "Не задан адрес или ключ Jellyfin")


class AddToCollectionRequest(BaseModel):
    collection_id: Annotated[str, Field(min_length=1, max_length=64)]
    series_ids: list[int] = Field(min_length=1, max_length=50)


@router.post(
    "/jellyfin/collections/add",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
async def add_to_jellyfin_collection(payload: AddToCollectionRequest) -> dict[str, Any]:
    """Добавляет тайтлы в существующую коллекцию."""
    from app.services.jellyfin import jellyfin

    _require_jellyfin()
    found, missing = await _resolve_items(payload.series_ids)
    if not found:
        raise ApiError(
            404,
            errors.JELLYFIN_NOTHING_FOUND,
            "Ни один из тайтлов не найден в Jellyfin — добавлять нечего",
        )
    if not await jellyfin.add_to_collection(payload.collection_id, found):
        raise ApiError(502, errors.JELLYFIN_UNAVAILABLE, "Jellyfin не принял добавление")
    return {"added": len(found), "missing": missing}


class CollectionRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    #: Тайтлы, которые надо собрать вместе. Каждый ищется в Jellyfin по пути.
    series_ids: list[int] = Field(min_length=1, max_length=50)


@router.post(
    "/jellyfin/collection",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
async def create_jellyfin_collection(payload: CollectionRequest) -> dict[str, Any]:
    """Собирает коллекцию в Jellyfin из указанных тайтлов.

    Запасной путь, а не основной: обычно Jellyfin делает это сам по данным
    TMDB. Кнопка нужна там, где TMDB тайтл не знает — а такое бывает.

    Тайтлы, которых Jellyfin у себя не нашёл, в коллекцию не попадают, и об
    этом сообщается отдельным числом: молча собрать половину и отчитаться
    успехом было бы хуже.
    """
    from app.services.jellyfin import jellyfin

    _require_jellyfin()
    found, missing = await _resolve_items(payload.series_ids)

    if not found:
        raise ApiError(
            404,
            errors.JELLYFIN_NOTHING_FOUND,
            "Ни один из тайтлов не найден в Jellyfin — собирать нечего",
        )

    result = await jellyfin.create_collection(payload.name, found)
    if result is None:
        raise ApiError(502, errors.JELLYFIN_UNAVAILABLE, "Jellyfin не создал коллекцию")

    return {"created": len(found), "missing": missing, "collection": result}


@router.get(
    "/mappings",
    dependencies=[Depends(require(Permission.CATALOG))],
)
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
                # Постер отдаётся ссылкой на Anime365 и меняется вместе с
                # привязкой сам: тайтл другой — и картинка другая, хранить
                # у себя нечего.
                "poster_url": series.poster_url,
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


@router.post(
    "/mappings",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def create_mapping(payload: ManualMapping) -> dict[str, Any]:
    """Ручная привязка. Помечается is_manual и не перетирается сканированием."""
    with session_scope() as session:
        folder = session.get(LibraryFolder, payload.folder_id)
        if folder is None:
            raise ApiError(404, errors.FOLDER_NOT_FOUND, "Папка не найдена")
        if session.get(Series, payload.series_id) is None:
            raise ApiError(404, errors.SERIES_NOT_FOUND, "Тайтл не найден")

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


@router.post(
    "/mappings/{mapping_id}/primary",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def make_primary(mapping_id: int, payload: PrimaryRequest) -> dict[str, Any]:
    """Назначает папку основной — туда будут складываться загрузки.

    Автоматически основной становится папка с наибольшим числом видео, но выбор
    можно переопределить: у одного сезона бывают две копии в разных кодеках, и
    какая из них «рабочая» — решает пользователь.
    """
    if not set_primary_mapping(payload.series_id, mapping_id):
        raise ApiError(404, errors.MAPPING_NOT_FOUND, "Связь не найдена у этого тайтла")
    return {"id": mapping_id, "is_primary": True}


@router.post(
    "/mappings/{mapping_id}/confirm",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def confirm_mapping(mapping_id: int) -> dict[str, Any]:
    """Подтверждение нечёткого совпадения — оно становится ручным."""
    with session_scope() as session:
        mapping = session.get(TitleMapping, mapping_id)
        if mapping is None:
            raise ApiError(404, errors.MAPPING_NOT_FOUND, "Связь не найдена")
        mapping.needs_confirmation = False
        mapping.is_manual = True
        return {"id": mapping_id, "confirmed": True}


@router.delete(
    "/mappings/{mapping_id}",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def delete_mapping(mapping_id: int) -> dict[str, Any]:
    with session_scope() as session:
        mapping = session.get(TitleMapping, mapping_id)
        if mapping is None:
            raise ApiError(404, errors.MAPPING_NOT_FOUND, "Связь не найдена")
        session.delete(mapping)
    return {"deleted": mapping_id}


@router.post(
    "/parse-test",
    # Побочных действий нет, но это инструмент настройки правил разбора имён —
    # тот же круг задач, что скан и привязки. Право `library`.
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def parse_test(
    names: Annotated[list[str], Body(embed=True)],
    scope: Annotated[str, Body(embed=True)] = "series",
) -> dict[str, Any]:
    """Пробный разбор имён без сканирования.

    Рабочий цикл настройки правил: сюда скармливается список файлов, в ответе
    видно, что каким правилом разобралось и что осталось нераспознанным.
    """
    if len(names) > 5000:
        raise ApiError(400, errors.TOO_MANY_NAMES, "За раз не более 5000 имён", max=5000)

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


@router.post(
    "/reload-rules",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def reload_parsing_rules() -> dict[str, Any]:
    """Перечитывает config/filename_rules.yaml без перезапуска сервиса."""
    rules = reload_rules()
    return {"rules": len(rules.rules), "exclude_patterns": len(rules.exclude)}


@router.get(
    "/series/{series_id}/files",
    dependencies=[Depends(require(Permission.CATALOG))],
)
def series_files(
    series_id: int,
    query: Annotated[str, Query(max_length=200, description="Искать по всей библиотеке")] = "",
) -> list[dict[str, Any]]:
    """Локальные файлы всех папок тайтла — кандидаты для ручной привязки серии.

    Показываем не только нераспознанные: перепривязать бывает нужно и файл,
    который разобрался, но не в ту серию (сдвиг нумерации у релиз-группы —
    обычное дело).

    С непустым `query` отбор идёт по **всей** библиотеке, а не по папкам тайтла.
    Так решается случай, когда часть тайтла лежит отдельно: вторая часть
    «Girls Band Cry» — своя папка в корне фильмов, к тайтлу не привязанная, и в
    окрестности первой части её не найти. Привязка серии к файлу папку тайтла
    не требует, поэтому и выбор ограничивать ею незачем.
    """
    needle = query.strip().lower()
    with session_scope() as session:
        stmt = (
            select(LibraryFile, LibraryFolder)
            .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
            .where(LibraryFile.kind.in_(("video", "subtitle")))
            .order_by(LibraryFile.filename)
        )
        if needle:
            # `ilike` в SQLite регистронезависим только для латиницы, поэтому
            # приводим сами — иначе кириллица в имени файла не найдётся.
            pattern = f"%{needle}%"
            stmt = stmt.where(
                func.lower(LibraryFile.filename).like(pattern)
                | func.lower(LibraryFolder.rel_path).like(pattern)
            ).limit(FILE_SEARCH_LIMIT)
        else:
            folder_ids = select(TitleMapping.folder_id).where(
                TitleMapping.series_id == series_id
            )
            stmt = stmt.where(LibraryFile.folder_id.in_(folder_ids))
        rows = session.execute(stmt).all()

        manual = {
            m.file_id: m
            for m in session.execute(
                select(EpisodeMapping).where(
                    EpisodeMapping.series_id == series_id, EpisodeMapping.is_manual.is_(True)
                )
            ).scalars()
        }

        return [
            {
                "file_id": file.id,
                "filename": file.filename,
                "folder": folder.rel_path,
                "kind": file.kind,
                "lang": file.lang,
                "size_bytes": file.size_bytes,
                "episode": file.episode,
                "matched_rule_id": file.matched_rule_id,
                "is_unmatched": file.is_unmatched,
                # Ручная привязка помечена отдельно: её автоскан не перетирает.
                "manual_episode": manual[file.id].episode if file.id in manual else None,
            }
            for file, folder in rows
        ]


class ManualEpisodeMapping(BaseModel):
    series_id: int
    episode: float
    file_id: int
    # Роль определяется по файлу, но её можно задать явно: у субтитров без
    # языкового суффикса автоопределение даёт sub_unknown.
    role: str | None = Field(None, pattern="^(video|sub_ru|sub_ja|sub_en|sub_unknown)$")


class EpisodeTargetRequest(BaseModel):
    series_id: int
    episode: float
    folder_id: int


@router.get(
    "/episode-targets/{series_id}",
    dependencies=[Depends(require(Permission.CATALOG))],
)
def list_episode_targets(series_id: int) -> dict[str, Any]:
    """Серии этого тайтла, у которых своя папка назначения.

    Нужно интерфейсу: об этом надо предупредить прямо на странице тайтла, а не
    прятать в отдельный экран — иначе батч «все серии» разложит файлы не туда,
    куда ожидает пользователь, и понять это можно будет только по факту.
    """
    with session_scope() as session:
        rows = session.execute(
            select(EpisodeTarget, LibraryFolder, LibraryRoot)
            .join(LibraryFolder, EpisodeTarget.folder_id == LibraryFolder.id)
            .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
            .where(EpisodeTarget.series_id == series_id)
            .order_by(EpisodeTarget.episode)
        ).all()

        return {
            "targets": [
                {
                    "id": target.id,
                    "episode": target.episode,
                    "folder_id": folder.id,
                    "rel_path": folder.rel_path,
                    "root_path": root.path,
                    "full_path": f"{root.path}/{folder.rel_path}",
                    "writable": root.is_writable,
                }
                for target, folder, root in rows
            ]
        }


@router.get(
    "/episode-targets/{series_id}/folders",
    dependencies=[Depends(require(Permission.CATALOG))],
)
def episode_target_folders(
    series_id: int,
    query: Annotated[str, Query(max_length=200, description="Искать по всей библиотеке")] = "",
) -> list[dict[str, Any]]:
    """Папки, куда можно направить отдельную серию.

    По умолчанию берём не все папки библиотеки, а окрестность тайтла:
    привязанные к нему плюс их соседи по родителю. Соседи нужны обязательно —
    типичный случай это `Specials` рядом с `Season 1`, а она привязана к другому
    `series_id` и в список «папок тайтла» не попала бы.

    ⚠️ Окрестности мало в двух случаях, и оба живые: у тайтла **нет ни одной**
    привязанной папки (тогда собирать окрестность не из чего, и раньше окно
    молча оказывалось пустым), и нужная папка лежит **в стороне** — вторая часть
    «Girls Band Cry» это отдельная папка в корне фильмов. Поэтому непустой
    `query` ищет по всей библиотеке; выдача ограничена, полный список тысяч
    папок здесь по-прежнему бесполезен.
    """
    needle = query.strip().lower()
    with session_scope() as session:
        own = (
            session.execute(
                select(LibraryFolder)
                .join(TitleMapping, TitleMapping.folder_id == LibraryFolder.id)
                .where(TitleMapping.series_id == series_id)
            )
            .scalars()
            .all()
        )

        if needle:
            found = (
                session.execute(
                    select(LibraryFolder)
                    .where(func.lower(LibraryFolder.rel_path).like(f"%{needle}%"))
                    .order_by(LibraryFolder.video_count.desc())
                    .limit(FOLDER_SEARCH_LIMIT)
                )
                .scalars()
                .all()
            )
            by_id = {f.id: f for f in found}
        else:
            if not own:
                return []
            # Родители и соседи: и сама папка тайтла, и сёстры вроде Specials.
            parent_ids = {f.parent_id for f in own if f.parent_id} | {f.id for f in own}
            siblings = (
                session.execute(
                    select(LibraryFolder).where(
                        LibraryFolder.parent_id.in_(parent_ids)
                        | LibraryFolder.id.in_(parent_ids)
                    )
                )
                .scalars()
                .all()
            )
            by_id = {f.id: f for f in [*own, *siblings]}
        roots = {
            r.id: r
            for r in session.execute(
                select(LibraryRoot).where(
                    LibraryRoot.id.in_({f.root_id for f in by_id.values()})
                )
            ).scalars()
        }

        out = []
        for folder in by_id.values():
            root = roots.get(folder.root_id)
            out.append(
                {
                    "folder_id": folder.id,
                    "rel_path": folder.rel_path,
                    "name": folder.name,
                    "season_number": folder.season_number,
                    "video_count": folder.video_count,
                    # Том только для чтения показываем, но выбрать не даём:
                    # назначение туда не упадёт сейчас — оно упадёт при загрузке.
                    "writable": bool(root and root.is_writable),
                    "is_title_folder": any(f.id == folder.id for f in own),
                }
            )
        out.sort(key=lambda item: item["rel_path"])
        return out


@router.delete(
    "/episode-targets/series/{series_id}",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def clear_episode_targets(series_id: int) -> dict[str, int]:
    """Снимает все назначения тайтла разом.

    Отдельно от построчного сброса намеренно: у длинного сериала назначений
    бывает два десятка, и убирать их по одному — это двадцать подтверждений.
    """
    with session_scope() as session:
        rows = (
            session.execute(select(EpisodeTarget).where(EpisodeTarget.series_id == series_id))
            .scalars()
            .all()
        )
        for row in rows:
            session.delete(row)
        return {"deleted": len(rows)}


@router.post(
    "/episode-targets",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def create_episode_target(payload: EpisodeTargetRequest) -> dict[str, Any]:
    """Назначает серии отдельную папку — вместо общей папки тайтла.

    Не путать с `episode-mappings`: та связывает серию с уже существующим
    файлом. Здесь речь о том, куда положить то, чего ещё нет.
    """
    with session_scope() as session:
        folder = session.get(LibraryFolder, payload.folder_id)
        if folder is None:
            raise ApiError(404, errors.FOLDER_NOT_FOUND, "Папка не найдена")

        root = session.get(LibraryRoot, folder.root_id)
        if root is not None and not root.is_writable:
            raise ApiError(
                409,
                errors.ROOT_READ_ONLY,
                f"Папка {root.path} смонтирована только для чтения — загрузка невозможна",
                path=root.path,
            )

        existing = session.execute(
            select(EpisodeTarget).where(
                EpisodeTarget.series_id == payload.series_id,
                EpisodeTarget.episode == payload.episode,
            )
        ).scalar_one_or_none()

        if existing is not None:
            # Переназначение, а не вторая запись: две папки на одну серию
            # означали бы, что предсказать место файла нельзя.
            existing.folder_id = payload.folder_id
            session.flush()
            return {"id": existing.id, "created": False}

        target = EpisodeTarget(
            series_id=payload.series_id,
            episode=payload.episode,
            folder_id=payload.folder_id,
        )
        session.add(target)
        session.flush()
        return {"id": target.id, "created": True}


@router.delete(
    "/episode-targets/{target_id}",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def delete_episode_target(target_id: int) -> dict[str, Any]:
    """Снимает назначение — серия вернётся в общую папку тайтла."""
    with session_scope() as session:
        target = session.get(EpisodeTarget, target_id)
        if target is None:
            raise ApiError(404, errors.MAPPING_NOT_FOUND, "Назначение не найдено")
        session.delete(target)
    return {"deleted": target_id}


@router.post(
    "/episode-mappings",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def create_episode_mapping(payload: ManualEpisodeMapping) -> dict[str, Any]:
    """Привязывает конкретный файл к конкретной серии вручную.

    Нужно, когда разбор имени ошибся: у релиз-группы сдвинута нумерация, файл
    назван без номера или серия склеена из двух. Помечается `is_manual`, и
    сканирование её больше не трогает.
    """
    with session_scope() as session:
        file = session.get(LibraryFile, payload.file_id)
        if file is None:
            raise ApiError(404, errors.FILE_NOT_FOUND, "Файл не найден")
        if session.get(Series, payload.series_id) is None:
            raise ApiError(404, errors.SERIES_NOT_FOUND, "Тайтл не найден")

        role = payload.role
        if role is None:
            if file.kind == "video":
                role = "video"
            elif file.kind == "subtitle":
                role = SUB_ROLE.get(file.lang or "", "sub_unknown")
            else:
                raise ApiError(400, errors.BIND_KIND_UNSUPPORTED, "Привязывать можно только видео и субтитры")

        # Прежние связи этого файла с этим тайтлом убираем целиком: файл — это
        # одна серия в одной роли, а не несколько сразу.
        session.execute(
            delete(EpisodeMapping).where(
                EpisodeMapping.series_id == payload.series_id,
                EpisodeMapping.file_id == payload.file_id,
            )
        )
        mapping = EpisodeMapping(
            series_id=payload.series_id,
            episode=payload.episode,
            file_id=payload.file_id,
            role=role,
            is_manual=True,
        )
        session.add(mapping)
        session.flush()
        return {"id": mapping.id, "episode": payload.episode, "role": role}


@router.delete(
    "/episode-mappings/{mapping_id}",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def delete_episode_mapping(mapping_id: int) -> dict[str, Any]:
    """Снимает ручную привязку. Автоматическая вернётся на следующем скане."""
    with session_scope() as session:
        mapping = session.get(EpisodeMapping, mapping_id)
        if mapping is None:
            raise ApiError(404, errors.MAPPING_NOT_FOUND, "Связь не найдена")
        session.delete(mapping)
    return {"deleted": mapping_id}


@router.get(
    "/mapping-report",
    dependencies=[Depends(require(Permission.CATALOG))],
)
def mapping_report() -> dict[str, Any]:
    """Чем именно смапились папки и с какой уверенностью.

    Нужен, чтобы подкручивать пороги по фактам, а не на глаз: если почти всё
    легло нечётким совпадением у самой границы, порог стоит поднять; если
    много неразобранного — опустить.
    """
    with session_scope() as session:
        by_source = session.execute(
            select(TitleMapping.source, func.count(TitleMapping.id))
            .group_by(TitleMapping.source)
            .order_by(func.count(TitleMapping.id).desc())
        ).all()

        # Гистограмма уверенности по десяткам — точнее здесь не нужно,
        # а полоса из ста столбиков нечитаема.
        buckets: dict[str, int] = {}
        for confidence, count in session.execute(
            select(TitleMapping.confidence, func.count(TitleMapping.id)).group_by(
                TitleMapping.confidence
            )
        ).all():
            low = int((confidence or 0) // 10 * 10)
            key = f"{low}–{min(low + 9, 100)}"
            buckets[key] = buckets.get(key, 0) + count

        # Тот же вычет контейнеров, что и в /status: три места считали одно и то
        # же по-разному, и статистика расходилась со списком.
        containers = session.execute(
            select(func.count()).select_from(_container_folder_ids().subquery())
        ).scalar_one()
        total_folders = (
            session.execute(select(func.count(LibraryFolder.id))).scalar_one() - containers
        )
        mapped_folders = session.execute(
            select(func.count(func.distinct(TitleMapping.folder_id)))
        ).scalar_one()
        manual = session.execute(
            select(func.count(TitleMapping.id)).where(TitleMapping.is_manual.is_(True))
        ).scalar_one()
        needs_confirmation = session.execute(
            select(func.count(TitleMapping.id)).where(TitleMapping.needs_confirmation.is_(True))
        ).scalar_one()

        # Самые слабые связи — с них начинается ручная проверка.
        weakest = session.execute(
            select(TitleMapping, LibraryFolder, Series)
            .join(LibraryFolder, TitleMapping.folder_id == LibraryFolder.id)
            .join(Series, TitleMapping.series_id == Series.id)
            .where(TitleMapping.is_manual.is_(False))
            .order_by(TitleMapping.confidence)
            .limit(10)
        ).all()

    return {
        "by_source": [{"source": source, "count": count} for source, count in by_source],
        "confidence_buckets": [
            {"range": key, "count": buckets[key]} for key in sorted(buckets, key=lambda k: -int(k.split("–")[0]))
        ],
        "folders_total": total_folders,
        "folders_mapped": mapped_folders,
        "folders_unmapped": max(0, total_folders - mapped_folders),
        "manual": manual,
        "needs_confirmation": needs_confirmation,
        "weakest": [
            {
                "mapping_id": m.id,
                "folder": f.rel_path,
                "series_id": s.id,
                "series_title": (s.titles or {}).get("romaji") or s.title,
                "poster_url": s.poster_url,
                "source": m.source,
                "confidence": m.confidence,
                "matched_on": m.matched_on,
            }
            for m, f, s in weakest
        ],
    }


@router.get(
    "/series/{series_id}/episodes/{episode}/files",
    dependencies=[Depends(require(Permission.CATALOG))],
)
def episode_files(series_id: int, episode: float) -> list[dict[str, Any]]:
    """Что лежит на диске за конкретной серией: путь каждого файла и дорожки.

    Заведено по просьбе: «по значку у серии открывать окно с путём каждого
    файла и данными ffprobe». Значок в строке серии показывает наличие, но не
    отвечает на вопросы «а какой именно это файл» и «что у него внутри» — а
    именно они возникают, когда что-то смапилось не туда.

    Путь собирается из корня, пути папки и имени файла: в базе он хранится
    разложенным, а человеку нужен целиком, чтобы найти файл руками.
    """
    with session_scope() as session:
        rows = session.execute(
            select(EpisodeMapping, LibraryFile, LibraryFolder, LibraryRoot)
            .join(LibraryFile, EpisodeMapping.file_id == LibraryFile.id)
            .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
            .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
            .where(
                EpisodeMapping.series_id == series_id,
                EpisodeMapping.episode == episode,
            )
            .order_by(EpisodeMapping.role, LibraryFile.filename)
        ).all()

        return [
            {
                "file_id": file.id,
                "filename": file.filename,
                "path": str(Path(root.path) / folder.rel_path / file.filename),
                "folder": folder.rel_path,
                "role": mapping.role,
                "kind": file.kind,
                "lang": file.lang,
                # Признак нужен интерфейсу, чтобы отличить выбор человека от
                # догадки автоматики: сбрасывать имеет смысл только первое.
                "lang_is_manual": file.lang_is_manual,
                "size_bytes": file.size_bytes,
                "mtime": file.mtime,
                "is_manual": mapping.is_manual,
                "matched_rule_id": file.matched_rule_id,
                "release_group": file.release_group,
                "anime365_translation_id": file.anime365_translation_id,
                # Дорожки могут быть и из `.nfo` (бесплатно), и от ffprobe.
                # Потребителю всё равно, откуда, но «смотрели ли уже» важно:
                # пусто при непустом `probed_at` значит «смотрели, не нашли».
                "streams": file.streams,
                "probed_at": file.probed_at.isoformat() if file.probed_at else None,
            }
            for mapping, file, folder, root in rows
        ]


class FileLanguage(BaseModel):
    #: `null` — вернуть определение автоматике.
    lang: Annotated[str | None, Field(max_length=8)] = None


@router.post(
    "/files/{file_id}/language",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
def set_file_language(file_id: int, payload: FileLanguage) -> dict[str, Any]:
    """Язык субтитров, указанный человеком.

    Автоматика определяет язык двумя способами — точно по `t{id}` и по
    письменности, — но остаётся третий случай: файл без суффикса, со смешанным
    текстом или вовсе на четвёртом языке. Тогда решает человек.

    ⚠️ `other` — это **не** «неизвестно». `sub_unknown` значит «не смогли
    определить» и зовёт разобраться; `other` значит «определили, это не
    ru/en/ja». Первое пользователь должен видеть в списке недоделанного, второе
    — нет, и субтитры при этом никуда не подтягиваются, а просто лежат.
    """
    from app.services.library_service import (
        OTHER_LANG,
        SUB_OTHER_ROLE,
        SUB_ROLE,
        _file_role,
    )

    allowed = set(SUB_ROLE) | {OTHER_LANG}
    lang = (payload.lang or "").strip().lower() or None
    if lang is not None and lang not in allowed:
        raise ApiError(
            400,
            errors.BIND_KIND_UNSUPPORTED,
            f"Язык должен быть одним из: {', '.join(sorted(allowed))}, либо пустым",
        )

    with session_scope() as session:
        file = session.get(LibraryFile, file_id)
        if file is None:
            raise ApiError(404, errors.FILE_NOT_FOUND, "Файл не найден")
        if file.kind != "subtitle":
            raise ApiError(
                400, errors.BIND_KIND_UNSUPPORTED, "Язык задаётся только у субтитров"
            )

        file.lang = lang
        # Пустое значение возвращает файл автоматике: на следующем скане язык
        # определится заново, а до тех пор останется тем, что стоит сейчас.
        file.lang_is_manual = lang is not None
        session.flush()

        role = _file_role(file)
        # Роль правим сразу, не дожидаясь скана: человек нажал и должен увидеть
        # результат, а не «через сутки».
        session.execute(
            EpisodeMapping.__table__.update()
            .where(EpisodeMapping.file_id == file_id)
            .values(role=role)
        )
        return {
            "file_id": file_id,
            "lang": lang,
            "role": role,
            "is_manual": file.lang_is_manual,
        }


@router.post(
    "/files/{file_id}/probe",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
async def probe_one_file(file_id: int) -> dict[str, Any]:
    """Разбирает ffprobe один файл — по кнопке в окне серии.

    Массовый прогон остаётся фоновой джобой: ffprobe читает не файл целиком, а
    заголовок (замерено 0,37 МБ из 37-мегабайтного файла и 45,9 мс на процесс),
    но дорог не объём, а характер — `fork/exec` и мелкие чтения вразнобой по
    диску, с которого одновременно качают и стримят. Один файл по явной просьбе
    человека этого не стоит: он ждёт осознанно.
    """
    from app.core.config import settings
    from app.services.audio import AudioError, ffmpeg_available, probe_stream_languages

    if not settings.get_bool("enable_ffprobe"):
        raise ApiError(
            409,
            errors.FFPROBE_DISABLED,
            "Определение дорожек через ffprobe выключено. Включите его в настройках",
        )
    if not ffmpeg_available():
        raise ApiError(503, errors.FFMPEG_MISSING, "В образе нет ffmpeg")

    with session_scope() as session:
        row = session.execute(
            select(LibraryFile, LibraryFolder, LibraryRoot)
            .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
            .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
            .where(LibraryFile.id == file_id)
        ).first()
        if row is None:
            raise ApiError(404, errors.FILE_NOT_FOUND, "Файл не найден")
        file, folder, root = row
        path = Path(root.path) / folder.rel_path / file.filename
        mtime = file.mtime

    from app.services.audio import probe_media_info

    try:
        streams = await probe_stream_languages(path)
        # Второй заход по тому же файлу, но за другим: кодеки, разрешение,
        # длительность. Языки идут в маппинг, а это — человеку в окно, и
        # смешивать их в одну структуру значило бы засорить маппинг.
        media = await probe_media_info(path)
    except AudioError as exc:
        raise ApiError(422, errors.PROBE_FAILED, f"ffprobe не смог прочитать файл: {exc}")

    # Запись — тем же методом, что у фоновой джобы: одна логика на оба пути,
    # включая отметку «смотрели» при пустом результате.
    stream_probe._save(file_id, streams, mtime)
    return {"file_id": file_id, "streams": streams, "media": media}


@router.post(
    "/series/{series_id}/probe-streams",
    dependencies=[Depends(require(Permission.LIBRARY))],
)
async def probe_streams(
    series_id: int,
    force: Annotated[bool, Query(description="Перепроверить и уже проверенные файлы")] = False,
) -> dict[str, Any]:
    """Определяет языки дорожек через ffprobe для файлов тайтла.

    Обычно не нужен: Jellyfin пишет список дорожек в `<streamdetails>` внутри
    `.nfo`, и это бесплатно. ffprobe же открывает каждый файл, поэтому работает
    только по кнопке, только для одного тайтла и только для тех файлов, у
    которых данных ещё нет.
    """
    from app.core.config import settings
    from app.services.audio import ffmpeg_available

    if not settings.get_bool("enable_ffprobe"):
        raise ApiError(
            409,
            errors.FFPROBE_DISABLED,
            "Определение дорожек через ffprobe выключено. Включите его в настройках, "
            "если у части файлов нет .nfo или в нём нет streamdetails",
        )
    if not ffmpeg_available():
        raise ApiError(503, errors.FFMPEG_MISSING, "В образе нет ffmpeg")
    if stream_probe.running:
        raise ApiError(409, errors.SCAN_RUNNING, "Определение дорожек уже идёт")

    # Тот же сервис, что и у фоновой джобы: одна логика отбора кандидатов и
    # одна запись результата. Здесь он вызывается для одного тайтла, поэтому
    # ждать ответа приемлемо — файлов десятки, а не тысячи.
    return await stream_probe.run(series_id=series_id, force=force)
