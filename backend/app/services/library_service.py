"""Сохранение результатов сканирования и автоматический маппинг."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import session_scope
from app.models.catalog import Episode, ScanRun, Series, Translation
from app.models.library import (
    EpisodeMapping,
    LibraryFile,
    LibraryFolder,
    LibraryRoot,
    TitleMapping,
)
from app.services.filename_rules import get_rules
from app.services.library_scan import (
    PARSER_REVISION,
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

#: Язык, который нас не интересует, но и «неизвестным» не является.
#:
#: Разница существенная: `sub_unknown` значит «не смогли определить» и зовёт
#: разобраться, а `other` — «определили, это не ru/en/ja». Первое пользователь
#: должен видеть в списке недоделанного, второе — нет.
OTHER_LANG = "other"
SUB_OTHER_ROLE = "sub_other"


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
        # ⚠️ Ручной язык скан не трогает. Автоматика ошибается на файлах без
        # суффикса и на смешанном тексте, а человек уже сказал, как есть.
        if not row.lang_is_manual:
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


def _forget_vanished_folders(session: Session, root_id: int, result: RootScanResult) -> int:
    """Убирает из БД папки, которых на диске больше нет.

    Файлы исчезнувшие удалялись, а папки — никогда, и база копила призраков.
    Живой пример: `Girls Band Cry/Movie 1 …` и `Girls Band Cry/Movie 2 …`
    переехали из корня сериалов в корень фильмов, но старые строки остались —
    вместе с файлами и привязками. Те же файлы считались дважды, а привязка
    указывала на несуществующий путь, из-за чего страница тайтла обещала
    локальные серии, которых не открыть.

    Обход каталогов идёт полностью при любом режиме (инкремент экономит только
    на разборе файлов), поэтому «нет в результате прохода» здесь действительно
    значит «нет на диске».

    ⚠️ Кроме поддеревьев, которые не удалось прочитать: там пустой список
    означает «неизвестно». Их не трогаем, иначе отвалившееся на секунду
    монтирование стёрло бы половину библиотеки.
    """
    seen: set[str] = set()
    unreadable: list[str] = []

    def walk(folder: ScannedFolder) -> None:
        seen.add(folder.rel_path)
        if folder.read_failed:
            unreadable.append(folder.rel_path + "/")
        for child in folder.children:
            walk(child)

    for folder in result.folders:
        walk(folder)

    rows = session.execute(
        select(LibraryFolder.id, LibraryFolder.rel_path).where(LibraryFolder.root_id == root_id)
    ).all()
    vanished = [
        folder_id
        for folder_id, rel_path in rows
        if rel_path not in seen and not any(rel_path.startswith(p) for p in unreadable)
    ]
    if not vanished:
        return 0

    # Каскады уносят вложенные папки, файлы и привязки — ручные в том числе:
    # папки нет, держать привязку к ней не за что.
    session.execute(delete(LibraryFolder).where(LibraryFolder.id.in_(vanished)))
    log.info("Забыто папок, исчезнувших с диска: %d", len(vanished))
    return len(vanished)


def _persist_root(result: RootScanResult) -> int:
    with session_scope() as session:
        root = session.execute(
            select(LibraryRoot).where(LibraryRoot.path == result.path)
        ).scalar_one_or_none()
        if root is None:
            root = LibraryRoot(path=result.path, kind=result.kind)
            session.add(root)

        root.kind = result.kind
        root.parser_revision = PARSER_REVISION
        root.is_available = result.is_available
        root.is_writable = result.is_writable
        root.folder_count = len(result.folders)
        root.scanned_at = datetime.utcnow()
        session.flush()
        root_id = root.id

        for folder in result.folders:
            _persist_folder(session, root_id, folder)

        session.flush()
        _forget_vanished_folders(session, root_id, result)

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


#: По сколько папок писать за одну транзакцию.
#:
#: Не про скорость, а про длительность writer-lock. Полсотни папок — доли
#: секунды; всё остальное в это время (загрузки, интерфейс) продолжает жить.
MAPPING_WRITE_BATCH = 50


def _map_folders(root_id: int, index: TitleIndex, scope: str) -> dict[str, int]:
    """Проставляет маппинг для всех папок корня.

    Сезонные подпапки мапятся отдельно от папки тайтла: в Anime365 каждый сезон
    это отдельный тайтл. Ручные привязки не трогаются.

    ## Почему в три прохода, а не в один

    Раньше всё это было в **одной** `session_scope()`, и нечёткое сопоставление
    крутилось прямо под открытой пишущей транзакцией. Замерено в контейнере:
    0,83 секунды на семи папках эталонной библиотеки, то есть около 0,12 с на
    папку — на библиотеке из тысячи папок это **две минуты** удержания
    writer-lock. SQLite допускает одного писателя, `busy_timeout` 10 секунд, и
    всё остальное в это время получало «database is locked».

    Теперь: короткое чтение → счёт без базы → короткие записи порциями.

    **Целостность от этого не страдает.** Удаление старой автопривязки и
    вставка новой для одной папки по-прежнему в одной транзакции, поэтому
    «папка без привязки» снаружи не наблюдается никогда. Разные папки могут
    какое-то время быть в разном состоянии — обе версии корректны, и ни одна
    ни на что не ссылается. Обрыв посередине оставит часть папок с прежними
    привязками; следующий скан доведёт дело до конца. Прежний вариант при
    обрыве откатывал вообще всё, то есть тоже не давал завершённости.
    """
    matcher = Matcher(
        index,
        auto_threshold=settings.get_int("fuzzy_auto_threshold"),
        suggest_threshold=settings.get_int("fuzzy_suggest_threshold"),
    )
    stats = {"auto": 0, "suggested": 0, "unmatched": 0, "manual_kept": 0}

    # ---- 1. Читаем всё, что нужно для счёта, и отпускаем базу.
    with session_scope() as session:
        folders = (
            session.execute(select(LibraryFolder).where(LibraryFolder.root_id == root_id))
            .scalars()
            .all()
        )
        by_id = {f.id: f for f in folders}
        manual_ids = {
            row[0]
            for row in session.execute(
                select(TitleMapping.folder_id).where(
                    TitleMapping.folder_id.in_([f.id for f in folders]),
                    TitleMapping.is_manual.is_(True),
                )
            ).all()
        }

        # Номера сезонов, на которых сошлись имена видеофайлов папки. Нужны
        # там, где сезон не назван больше нигде: в `Classroom of the Elite`
        # лежат 17 файлов `S04E01…` без единой подпапки, и папка уезжала на
        # первый сезон. Единогласие обязательно — разнобой ничего не значит.
        season_rows = session.execute(
            select(
                LibraryFile.folder_id,
                LibraryFile.season_number,
                func.count(),
            )
            .where(
                LibraryFile.folder_id.in_([f.id for f in folders] or [0]),
                LibraryFile.kind == "video",
                LibraryFile.excluded_reason.is_(None),
                LibraryFile.season_number.is_not(None),
            )
            .group_by(LibraryFile.folder_id, LibraryFile.season_number)
        ).all()
        seasons_seen: dict[int, set[int]] = {}
        for folder_id, season, _count in season_rows:
            seasons_seen.setdefault(folder_id, set()).add(int(season))

        # Имена видеофайлов — для папок спешлов. Сама папка называется
        # «Specials», а конкретный спешл назван в имени файла.
        names_seen: dict[int, list[str]] = {}
        for folder_id, filename in session.execute(
            select(LibraryFile.folder_id, LibraryFile.filename).where(
                LibraryFile.folder_id.in_([f.id for f in folders] or [0]),
                LibraryFile.kind == "video",
                LibraryFile.excluded_reason.is_(None),
            )
        ).all():
            names_seen.setdefault(folder_id, []).append(Path(filename).stem)

        # Подпапки, где нет видео, но есть субтитры: их привязка осмысленна —
        # субтитры надо к чему-то отнести.
        with_subtitles = {
            folder_id
            for (folder_id,) in session.execute(
                select(LibraryFile.folder_id)
                .where(
                    LibraryFile.folder_id.in_([f.id for f in folders] or [0]),
                    LibraryFile.kind == "subtitle",
                )
                .distinct()
            ).all()
        }

        # Папки, которые мапить нечем: их прежние привязки снимаются.
        skipped: list[int] = []

        # Контексты собираем здесь же: дальше объекты будут отсоединены от
        # сессии, и обращение к их полям стало бы неожиданным запросом.
        contexts: list[tuple[int, MatchContext]] = []
        for folder in folders:
            if folder.id in manual_ids:
                # Ручная привязка — решение пользователя, автоскан её не перетирает.
                stats["manual_kept"] += 1
                continue

            parent = by_id.get(folder.parent_id) if folder.parent_id else None
            # Папка сезона берёт название и внешние ID у родителя.
            #
            # ⚠️ Но только сезона. Подпапка без номера сезона — это фильм,
            # спешлы, бонусы или служебная папка вроде `subtitles`, и данные
            # родителя ей чужие. Поэтому рядом всегда передаём и собственные
            # имя с `.nfo`, а решает матчер
            # ([14-mapping-audit.md](../../../claude/14-mapping-audit.md)).
            seen_seasons = seasons_seen.get(folder.id, set())
            season_from_files = next(iter(seen_seasons)) if len(seen_seasons) == 1 else None

            title_nfo = _nfo_of(parent) if parent is not None else _nfo_of(folder)
            season_nfo = _nfo_of(folder) if parent is not None else None
            folder_name = parent.name if parent is not None else folder.name

            # ⚠️ Пустая ПОДПАПКА не мапится вовсе.
            #
            # У папки тайтла пустота осмысленна: тайтл заведён, файлов пока нет,
            # и привязка нужна, чтобы загрузки знали куда идти. А подпапка без
            # единого видео не описывает ничего — тайтл уже назван родителем.
            #
            # Живые примеры из библиотеки: `…/Extras`, `Toradora!/Specials/BD
            # Scans`. Там лежат картинки и сканы, а нечёткое сравнение всё
            # равно что-то находило: `BD Scans` уезжали на `MM! Specials` с
            # похожестью 76. Это чистый шум в списке «требует подтверждения».
            # ⚠️ Подпапка, ЗАЯВИВШАЯ номер сезона, остаётся даже пустой.
            #
            # Пустая `Season 2` — это заявление структуры: «сезон живёт здесь».
            # Отбросив её, мы при загрузке второго сезона не нашли бы привязки
            # и завели рядом НОВУЮ папку, хотя нужная уже есть. В Anime365
            # каждый сезон — отдельный тайтл, поэтому такая привязка и есть
            # единственная связь сезона с его местом на диске.
            #
            # Отбрасываем только то, что не заявляет ничего и ничего не
            # содержит: `BD Scans`, `Bonus` и прочие остатки переносов.
            if (
                parent is not None
                and not folder.video_count
                and folder.id not in with_subtitles
                and folder.season_number is None
            ):
                # ⚠️ Не просто пропускаем: привязку надо СНЯТЬ.
                #
                # Удаление старого идёт только по списку обработанных папок, и
                # пропущенная сохраняла бы прежнюю привязку навсегда. Именно так
                # `Toradora!/Specials/BD Scans` продолжала висеть на
                # `MM! Specials` после того, как правило её отсекло.
                skipped.append(folder.id)
                continue

            contexts.append(
                (
                    folder.id,
                    MatchContext(
                        folder_name=folder_name,
                        scope=scope,
                        nfo=title_nfo,
                        season_nfo=season_nfo,
                        local_episode_count=folder.video_count,
                        own_name=folder.name,
                        own_nfo=_nfo_of(folder),
                        is_child=parent is not None,
                        video_count=folder.video_count,
                        own_season=folder.season_number,
                        season_from_files=season_from_files,
                        file_names=names_seen.get(folder.id, []),
                    ),
                )
            )

    # ---- 2. Считаем без единого обращения к базе. Самая долгая часть.
    matched = [(folder_id, matcher.match(ctx)) for folder_id, ctx in contexts]

    # ---- 3. Пишем порциями. Пара «удалить старое, вставить новое» для каждой
    # папки остаётся внутри одной транзакции.
    if skipped:
        with session_scope() as session:
            session.execute(
                delete(TitleMapping).where(
                    TitleMapping.folder_id.in_(skipped),
                    TitleMapping.is_manual.is_(False),
                )
            )
    for start in range(0, len(matched), MAPPING_WRITE_BATCH):
        batch = matched[start : start + MAPPING_WRITE_BATCH]
        with session_scope() as session:
            session.execute(
                delete(TitleMapping).where(
                    TitleMapping.folder_id.in_([folder_id for folder_id, _ in batch]),
                    TitleMapping.is_manual.is_(False),
                )
            )
            for folder_id, result in batch:
                if result is None:
                    stats["unmatched"] += 1
                    continue
                session.add(
                    TitleMapping(
                        series_id=result.series_id,
                        folder_id=folder_id,
                        source=result.source.value,
                        confidence=result.confidence,
                        matched_on=result.matched_on,
                        is_manual=False,
                        needs_confirmation=result.needs_confirmation,
                    )
                )
                stats["suggested" if result.needs_confirmation else "auto"] += 1

    # ---- 4. Дубликаты «контейнер против подпапки» — по полной картине, когда
    # все привязки уже записаны. Своей короткой транзакцией.
    with session_scope() as session:
        fresh = (
            session.execute(select(LibraryFolder).where(LibraryFolder.root_id == root_id))
            .scalars()
            .all()
        )
        stats["dropped_container"] = _drop_container_duplicates(session, fresh)

    return stats


#: Сколько своих видео папка-контейнер имеет право держать.
#:
#: Ноль — чистый контейнер. Один — контейнер, в который что-то случайно упало:
#: у `Toradora!` это спешл, попавший в корень при скачивании. Два и больше уже
#: означают, что часть серий действительно лежит здесь.
STRAY_VIDEO_LIMIT = 1


def _drop_container_duplicates(session, folders: list[LibraryFolder]) -> int:
    """Снимает привязку с папки-контейнера, если её же тайтл держит подпапка.

    Откуда берётся дубликат. `<anilistid>` лежит в `tvshow.nfo`, то есть в
    корневой папке шоу, и указывает на первый сезон. Папка `Season 1` своего ID
    не имеет и наследует родительский — так и задумано. Но ID остаётся и у
    самого корня, поэтому один и тот же тайтл оказывается привязан дважды: к
    `Show` и к `Show/Season 1`.

    Побеждает подпапка: в ней лежат файлы, а корень — контейнер. Если бы
    основной осталась корневая, загрузки первого сезона легли бы рядом с
    папками сезонов, а не внутрь нужной.

    Но уступает корень **не любому** потомку, а только тому, где файлов больше.
    Два условия:

    1. **У корня своих видео не больше одного** (`STRAY_VIDEO_LIMIT`). Ноль —
       чистый контейнер; один — контейнер, в который что-то случайно упало.
       Начиная с двух это уже раскладка, и снимать привязку нельзя.
    2. **У потомка видео больше.** Пустая подпапка не лучше корня ничем.

    ⚠️ Раньше первое условие было строже: видео у корня не должно было быть
    вовсе. На живой библиотеке этого не хватило. У `Toradora!` в корне лежит
    **один** файл — спешл `[Anime365] Toradora Bentou no Gokui - 01
    (t924861).mkv`, попавший туда при скачивании, — а в `Season 1` двадцать
    девять. Одного случайного файла хватало, чтобы корень считался рабочей
    папкой, и тайтл оставался привязан дважды: и к `Toradora!`, и к
    `Toradora!/Season 1`.

    Осиротевший файл при этом не теряется: у него в имени есть `t{id}`, и он
    опознаётся сам ([D-88](../../../claude/09-decisions.md)).

    Отдельного исключения для спешлов нет — решение пользователя. Следствие:
    при раскладке «корень без видео + только `Specials`» привязка уедет на
    `Specials`, и туда же пойдут загрузки, пока не появится папка сезона с
    файлами. Обсуждалось и принято осознанно: правило из двух условий
    предсказуемее, а раскладка редкая и самоисправляется.

    Если ни один потомок условиям не отвечает, обе привязки остаются, и какая
    из них основная, решает `ensure_primary_mapping()` по вкладу в тайтл.

    ⚠️ Пустую папку с одним `tvshow.nfo` это НЕ трогает: снимаем привязку
    только когда тот же тайтл держит подходящий потомок. Папки без единого
    видеофайла должны мапиться и показывать 0 серий — это отдельное
    требование, и сплошная проверка «нет видео → не мапить» его бы нарушила.

    Ручные привязки не трогаем никогда: это решение пользователя.
    """
    by_id = {f.id: f for f in folders}

    def is_ancestor(candidate_id: int, folder: LibraryFolder) -> bool:
        current = folder
        seen = set()
        while current.parent_id is not None and current.parent_id not in seen:
            seen.add(current.parent_id)
            if current.parent_id == candidate_id:
                return True
            current = by_id.get(current.parent_id)
            if current is None:
                return False
        return False

    rows = session.execute(
        select(TitleMapping).where(
            TitleMapping.folder_id.in_([f.id for f in folders]),
            TitleMapping.is_manual.is_(False),
        )
    ).scalars().all()

    by_series: dict[int, list[TitleMapping]] = {}
    for row in rows:
        by_series.setdefault(row.series_id, []).append(row)

    dropped = 0
    for mappings in by_series.values():
        if len(mappings) < 2:
            continue
        folder_ids = {m.folder_id for m in mappings}
        for mapping in mappings:
            folder = by_id.get(mapping.folder_id)
            if folder is None:
                continue
            if folder.video_count > STRAY_VIDEO_LIMIT:
                # Серии лежат прямо здесь — это рабочая папка, а не контейнер.
                continue

            has_better_descendant = any(
                other_id != mapping.folder_id
                and other_id in by_id
                and is_ancestor(mapping.folder_id, by_id[other_id])
                and by_id[other_id].video_count > folder.video_count
                for other_id in folder_ids
            )
            if has_better_descendant:
                session.delete(mapping)
                dropped += 1

    return dropped


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


def _file_role(file: LibraryFile) -> str | None:
    if file.kind == "video":
        return "video"
    if file.kind == "subtitle":
        if file.lang == OTHER_LANG:
            return SUB_OTHER_ROLE
        return SUB_ROLE.get(file.lang or "", "sub_unknown")
    return None


def _own_downloads(
    session: Session, files: list[LibraryFile]
) -> dict[int, tuple[int, float, str]]:
    """Файлы, назвавшие себя сами: `(series_id, номер серии, язык)` по `t{id}`.

    Файл `[Anime365] BanG Dream Movie Poppin Dream - 01 (t4000194).mkv` несёт
    идентификатор перевода, а перевод в каталоге привязан к тайтлу и эпизоду.
    Это точная связь, и она сильнее папки.

    Зачем: в корне фильмов есть папка `Bang Dream!`, в которую сложены **восемь
    разных фильмов** — «Poppin' Dream!», две части «Episode of Roselia», «FILM
    LIVE» и прочие. Одна папка на восемь тайтлов в модели «папка → тайтл» не
    раскладывается никак, а пять из восьми файлов знают свой тайтл сами.
    """
    wanted = {f.anime365_translation_id: f.id for f in files if f.anime365_translation_id}
    if not wanted:
        return {}

    rows = session.execute(
        select(
            Translation.id,
            Translation.series_id,
            Episode.episode_num,
            Translation.type_lang,
        )
        .join(Episode, Translation.episode_id == Episode.id, isouter=True)
        .where(Translation.id.in_(list(wanted)))
    ).all()

    out: dict[int, tuple[int, float, str]] = {}
    for translation_id, series_id, episode_num, type_lang in rows:
        if series_id is None or episode_num is None:
            continue
        out[wanted[translation_id]] = (series_id, float(episode_num), type_lang or "")
    return out


#: Номер части многосерийного фильма в имени папки.
#:
#: Свои папки мы называем «Тайтл (Год) - 2» (см. `folder_name_for_title`),
#: у пользователя и Jellyfin встречается «Movie 2», «Part 2».
_PART_PATTERNS = (
    re.compile(r"(?i)\b(?:movie|part|chapter|фильм|часть)\s*(\d{1,2})\b"),
    re.compile(r"-\s*(\d{1,2})\s*$"),
)


def _movie_part_number(folder_name: str) -> int | None:
    for pattern in _PART_PATTERNS:
        match = pattern.search(folder_name)
        if match:
            value = int(match.group(1))
            if value > 0:
                return value
    return None


def _episode_totals(session: Session, series_ids: set[int]) -> dict[int, int]:
    if not series_ids:
        return {}
    rows = session.execute(
        select(Series.id, Series.number_of_episodes).where(Series.id.in_(series_ids))
    ).all()
    return {series_id: total or 0 for series_id, total in rows}


def _episode_for(
    file: LibraryFile,
    folder: LibraryFolder,
    series_id: int,
    totals: dict[int, int],
) -> float:
    """Номер серии для файла в этом тайтле.

    Обычно это разобранный из имени номер. Особый случай — фильм из нескольких
    частей: у Girls Band Cry в каталоге одно вхождение с **двумя** сериями, а на
    диске две папки по одному видео. Каждое видео — «единственное в папке»,
    поэтому обе части получили бы первую серию: одна затёрла бы другую, вторая
    числилась бы отсутствующей.

    Номер части берётся из имени папки («Movie 2», «Part 2», наш хвост « - 2»)
    и только если в тайтле серий больше одной. У соседнего вхождения
    «Girls Band Cry Movie 2» серия ровно одна, и там та же папка обязана дать
    первую серию, а не вторую.
    """
    if file.episode != 1.0 or file.matched_rule_id != "single_movie":
        return file.episode
    if totals.get(series_id, 0) <= 1:
        return file.episode
    part = _movie_part_number(folder.name or "")
    return float(part) if part else file.episode


def _map_episodes(root_id: int) -> int:
    """Связывает локальные файлы с сериями.

    Обходятся **все** папки корня, а не только смапленные: файл со своим
    `t{id}` в имени опознаётся сам, даже если папка вокруг него не опознана.
    """
    linked = 0
    with session_scope() as session:
        folders = (
            session.execute(select(LibraryFolder).where(LibraryFolder.root_id == root_id))
            .scalars()
            .all()
        )
        mappings: dict[int, list[int]] = {}
        for folder_id, series_id in session.execute(
            select(TitleMapping.folder_id, TitleMapping.series_id).where(
                TitleMapping.folder_id.in_([f.id for f in folders] or [0])
            )
        ).all():
            mappings.setdefault(folder_id, []).append(series_id)

        for folder in folders:
            files = (
                session.execute(select(LibraryFile).where(LibraryFile.folder_id == folder.id))
                .scalars()
                .all()
            )
            file_ids = [f.id for f in files]
            if not file_ids:
                continue

            folder_series = mappings.get(folder.id, [])
            own = _own_downloads(session, files)
            if not folder_series and not own:
                continue
            totals = _episode_totals(session, set(folder_series))

            # Чистим по файлам, а не по тайтлу: файл мог сменить тайтл (папку
            # перепривязали, `t{id}` указал на другой) — старая строка иначе
            # осталась бы висеть, и серия числилась бы скачанной у обоих.
            session.execute(
                delete(EpisodeMapping).where(
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
                        EpisodeMapping.is_manual.is_(True),
                        EpisodeMapping.file_id.in_(file_ids),
                    )
                ).all()
            }

            for file in files:
                if file.excluded_reason or file.id in manual_files:
                    continue
                role = _file_role(file)
                if role is None:
                    continue

                exact = own.get(file.id)
                if exact is not None:
                    # Язык субтитров берётся у перевода, а не из имени файла.
                    # Точно и без догадок: `t{id}` ведёт к переводу, а у него
                    # есть `type_lang`. Так подписываются наши же файлы,
                    # скачанные до того, как языковой суффикс появился в
                    # правилах именования, — они лежали как `sub_unknown`.
                    if file.kind == "subtitle" and exact[2] in SUB_ROLE:
                        if file.lang != exact[2]:
                            file.lang = exact[2]
                        role = SUB_ROLE[exact[2]]
                    targets = [(exact[0], exact[1])]
                elif file.episode is not None:
                    targets = [
                        (series_id, _episode_for(file, folder, series_id, totals))
                        for series_id in folder_series
                    ]
                else:
                    continue

                for series_id, episode in targets:
                    session.add(
                        EpisodeMapping(
                            series_id=series_id,
                            episode=episode,
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
        if root.parser_revision != PARSER_REVISION:
            # Разбор изменился — то, что лежит в базе, разобрано по-старому.
            # Пропускать «неизменившиеся» папки нельзя: на диске они и правда
            # не менялись, а вот прочитать их надо заново.
            log.info(
                "Корень %s разобран версией %s, текущая %s — полный проход",
                root_path,
                root.parser_revision,
                PARSER_REVISION,
            )
            return None
        root_id = root.id
    return load_folder_states(root_id)
