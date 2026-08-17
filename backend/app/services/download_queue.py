"""Очередь загрузок: постановка задач, воркеры, постобработка.

Конвейер:

    queued → downloading → downloaded → postprocessing → done
                 ↓ ошибка                    ↓
              failed (бэкофф) → queued    failed

Постобработка вынесена отдельным шагом, потому что перемещение между дисками
может занимать заметное время, и блокировать им скачивание не нужно.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.db import session_scope
from app.core.events import bus
from app.core.paths import UnsafePathError, resolve_within
from app.models.catalog import Series, Translation
from app.models.downloads import ACTIVE_KEY, ACTIVE_STATUSES, DownloadTask, TaskStatus
from app.models.library import (
    EpisodeMapping,
    EpisodeTarget,
    LibraryFile,
    LibraryFolder,
    LibraryRoot,
    TitleMapping,
)
from app.services.anime365 import Anime365Error, SubscriptionRequired
from app.services.downloader import (
    DownloadCancelled,
    Downloader,
    check_free_space,
    pick_download_url,
)
from app.services.naming import (
    NamingContext,
    audio_name,
    build_target_name,
    folder_name_for_title,
    folder_name_source,
    pick_neighbour,
    unique_path,
)

log = logging.getLogger(__name__)

SUBTITLE_EXTENSION = ".ass"
VIDEO_EXTENSION = ".mp4"

#: Что вообще можно поставить в очередь.
#:
#: ⚠️ Набор проверяется при постановке, и это не формальность. Воркер узнаёт
#: субтитры по строгому равенству `kind == "subtitle"`, а всё остальное качает
#: как видео. Опечатка в вызывающем коде поэтому не падает, а **молча меняет
#: смысл задачи**: автоскачивание субтитров передавало `"subtitles"` во
#: множественном числе и выкачивало видеофайлы целиком вместо файлов с
#: субтитрами. Проверка здесь превращает такую ошибку в исключение на месте.
TASK_KINDS = frozenset({"video", "subtitle", "audio"})


class QueueError(RuntimeError):
    pass


class ReadOnlyLibrary(QueueError):
    """Целевая папка смонтирована только для чтения."""


class NoTargetFolder(QueueError):
    """Тайтл не привязан ни к одной папке — складывать некуда."""


def _resolve_layout(series, series_id: int) -> bool:
    """Раскладывать тайтл как фильм или как сериал.

    `movie` — фильм, включая связку из нескольких частей: каждая часть получает
    свою папку (см. `_layout_with_part`). Решение пользователя перевешивает
    автоопределение.
    """
    from app.services.coverage import resolve_is_movie
    from app.services.mapping_info import count_content_episodes

    return resolve_is_movie(series.treat_as_movie, series.type, count_content_episodes(series_id))


def _layout_with_part(series, series_id: int, episode: float | None) -> tuple[bool, int | None]:
    """Раскладка и номер части, который надо дописать к имени папки.

    Номер появляется только у многосерийного фильма. У одиночного он не нужен и
    мешал бы Jellyfin сопоставить папку с TMDB — а без такого сопоставления не
    соберётся и коллекция, которой Jellyfin связывает части между собой.

    Решение пользователя «считать сериалом» отменяет и номер: в сериале части
    и так разойдутся по S01E01 и S01E02.
    """
    from app.services.coverage import is_multipart_movie
    from app.services.mapping_info import count_content_episodes

    is_movie = _resolve_layout(series, series_id)
    if not is_movie or episode is None:
        return is_movie, None
    if not is_multipart_movie(series.type, count_content_episodes(series_id)):
        return is_movie, None
    return is_movie, int(episode)


#: Типы тайтла, которым место в `Specials`, а не в сезонной подпапке.
#:
#: `ona` сюда не входит: веб-сериалы это полноценные сезоны.
SPECIAL_SERIES_TYPES = frozenset({"special", "tv_special", "ova"})

#: Имя подпапки для спешлов. Так их называет Jellyfin, и так они уже лежат в
#: библиотеке пользователя (`Toradora!/Specials`).
SPECIALS_FOLDER = "Specials"


def desired_season_folder(series_id: int) -> str | None:
    """Как называется подпапка сезона для этого тайтла.

    Раскладка по сезонным подпапкам — решение пользователя, и она совпадает с
    тем, как раскладывают Sonarr и Jellyfin: `Show/Season 1`, `Show/Season 2`,
    `Show/Specials`. Держать серии прямо в папке шоу тоже допустимо, но тогда
    сезоны перемешиваются, а докачанное невозможно отличить от остального.

    В Anime365 каждый сезон — **отдельный тайтл**, поэтому номер берём из его
    названия («Grand Blue Season 3» → 3). Не назван — считаем первым: у шоу с
    единственным сезоном так и есть, а ошибиться здесь дёшево.

    Фильмы возвращают `None`: у них сезонов нет, каждая часть живёт своей
    папкой в корне фильмов.
    """
    from app.services.matcher import season_in_title

    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            return None
        if _resolve_layout(series, series_id):
            return None
        if (series.type or "") in SPECIAL_SERIES_TYPES:
            return SPECIALS_FOLDER
        titles = dict(series.titles or {})

    for value in titles.values():
        if not value:
            continue
        claimed = season_in_title(value)
        if claimed is not None:
            return f"Season {claimed}"
    return "Season 1"


def _root_uses_season_folders(folder: LibraryFolder) -> bool:
    """Есть ли у этой папки шоу хоть одна сезонная подпапка."""
    with session_scope() as session:
        rows = session.execute(
            select(LibraryFolder.season_number).where(
                LibraryFolder.parent_id == folder.id
            )
        ).scalars().all()
    return any(number is not None for number in rows)


def _folder_has_own_videos(folder: LibraryFolder) -> bool:
    return (folder.video_count or 0) > 0


def _ensure_season_subfolder(
    folder: LibraryFolder, root: LibraryRoot, series_id: int
) -> tuple[LibraryFolder, LibraryRoot]:
    """Подпапка сезона внутри папки шоу — существующая или новая.

    ⚠️ Заводим её не всегда. Если в самой папке шоу уже лежат серии, а
    сезонных подпапок нет, то новая подпапка **разорвала** бы сезон на два
    места: часть файлов осталась бы в корне. Jellyfin такое переживёт, а вот
    наш сканер увидит две папки на один сезон — ровно те двойники, что уже
    доставляют хлопот в библиотеке.

    Поэтому правило простое: подпапку создаём, когда папка шоу либо пуста
    (новый тайтл), либо уже раскладывается по сезонам. Иначе кладём к соседям.
    """
    name = desired_season_folder(series_id)
    if name is None:
        return folder, root

    # Папка уже сама является сезонной — ничего искать не надо.
    if folder.season_number is not None or folder.rel_path.endswith(f"/{name}"):
        return folder, root

    if _folder_has_own_videos(folder) and not _root_uses_season_folders(folder):
        log.info(
            "Тайтл %s: серии лежат прямо в %s, подпапку %s не заводим",
            series_id,
            folder.rel_path,
            name,
        )
        return folder, root

    rel_path = f"{folder.rel_path}/{name}" if folder.rel_path else name
    target = Path(root.path) / rel_path
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("Не удалось создать подпапку сезона %s: %s", target, exc)
        return folder, root

    number = None if name == SPECIALS_FOLDER else int(name.rsplit(" ", 1)[1])
    with session_scope() as session:
        child = session.execute(
            select(LibraryFolder).where(
                LibraryFolder.root_id == root.id, LibraryFolder.rel_path == rel_path
            )
        ).scalar_one_or_none()
        if child is None:
            child = LibraryFolder(
                root_id=root.id,
                parent_id=folder.id,
                rel_path=rel_path,
                name=name,
                season_number=0 if name == SPECIALS_FOLDER else number,
                video_count=0,
            )
            session.add(child)
            session.flush()

        # Привязку переносим на подпапку: загрузки должны идти туда же, где
        # лежат файлы, иначе следующая серия снова уедет в корень.
        exists = session.execute(
            select(TitleMapping).where(
                TitleMapping.series_id == series_id, TitleMapping.folder_id == child.id
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                TitleMapping(
                    series_id=series_id,
                    folder_id=child.id,
                    source="created",
                    confidence=100.0,
                    matched_on="подпапка сезона создана при загрузке",
                    is_manual=False,
                    is_primary=True,
                )
            )
        session.expunge(child)

    log.info("Тайтл %s: серии пойдут в %s", series_id, rel_path)
    return child, root


def _primary_folder(series_id: int) -> tuple[LibraryFolder, LibraryRoot] | None:
    """Основная папка тайтла — куда складывать скачанное."""
    # Импорт локальный: mapping_info тянет за собой модели библиотеки, а те —
    # обратно очередь, и на уровне модуля получился бы цикл.
    from app.services.mapping_info import ensure_primary_mapping

    ensure_primary_mapping(series_id)

    with session_scope() as session:
        row = session.execute(
            select(TitleMapping, LibraryFolder, LibraryRoot)
            .join(LibraryFolder, TitleMapping.folder_id == LibraryFolder.id)
            .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
            .where(TitleMapping.series_id == series_id, TitleMapping.is_primary.is_(True))
        ).first()
        if row is None:
            return None
        _, folder, root = row
        session.expunge(folder)
        session.expunge(root)
        return folder, root


def why_no_folder(series_id: int, is_movie: bool) -> str:
    """Почему складывать некуда — конкретной причиной, а не общей фразой.

    Причин несколько, и чинятся они по-разному: корня нет вовсе, корень
    смонтирован только для чтения, том отвалился, у тайтла нет пригодного
    названия. Одна фраза на все случаи заставляла гадать, что именно проверять.
    """
    root_kind = "movie" if is_movie else "series"
    with session_scope() as session:
        roots = (
            session.execute(select(LibraryRoot).where(LibraryRoot.kind == root_kind))
            .scalars()
            .all()
        )
        setting = "movies_root" if is_movie else "series_root"
        if not roots:
            return (
                f"Библиотека {'фильмов' if is_movie else 'сериалов'} не настроена: "
                f"задайте {setting} в настройках и дождитесь сканирования"
            )
        available = [r for r in roots if r.is_available]
        if not available:
            return (
                f"Корень {roots[0].path} недоступен — проверьте, что том примонтирован"
            )
        writable = [r for r in available if r.is_writable]
        if not writable:
            return (
                f"Корень {available[0].path} смонтирован только для чтения. "
                "Загрузка требует записи: перемонтируйте его без :ro"
            )
    return (
        "У тайтла нет названия, пригодного для имени папки. "
        "Привяжите папку вручную в плашке на странице тайтла"
    )


def propose_folder(series_id: int) -> dict[str, Any] | None:
    """Куда уедет загрузка, если тайтл ещё не привязан к папке.

    Ровно тот же расчёт, что делает `ensure_target_folder`, но БЕЗ побочных
    эффектов: ничего не создаётся и не привязывается. Нужен, чтобы показать
    имя будущей папки до нажатия «скачать», а не после — иначе о том, куда
    легли файлы, узнают уже по факту.

    Возвращает `None`, когда предлагать нечего: тайтл уже привязан, подходящего
    корня нет или он только для чтения. Во всех этих случаях в интерфейсе
    уместно другое сообщение, а не имя папки.
    """
    if _primary_folder(series_id) is not None:
        return None

    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            return None
        titles = dict(series.titles or {})
        # Связка фильмов тоже уходит в киношную библиотеку — каждой части своя
        # папка. См. coverage.auto_is_movie и _layout_with_part.
        is_movie = _resolve_layout(series, series_id)
        year = series.year
        series_type = series.type

    from app.services.coverage import is_multipart_movie
    from app.services.mapping_info import count_content_episodes

    # У многосерийного фильма единой папки не будет вовсе: каждая часть получает
    # свою. Показать здесь имя без номера значило бы пообещать не то, что
    # создастся, — а этот расчёт для того и существует, чтобы узнать заранее.
    per_part = is_movie and is_multipart_movie(series_type, count_content_episodes(series_id))

    folder_name = folder_name_for_title(
        titles, year=year, is_movie=is_movie, part=1 if per_part else None
    )
    if not folder_name:
        return None

    root_kind = "movie" if is_movie else "series"
    with session_scope() as session:
        root = session.execute(
            select(LibraryRoot).where(
                LibraryRoot.kind == root_kind, LibraryRoot.is_available.is_(True)
            )
        ).scalars().first()
        if root is None:
            return None
        root_path, writable = root.path, root.is_writable

    return {
        "folder_name": folder_name,
        # Имя показано для первой части; у остальных отличается номером.
        # Интерфейс обязан сказать это вслух, иначе человек ждёт одну папку.
        "per_part": per_part,
        # Откуда взято написание: en | romaji | ru. Интерфейс называет источник
        # прямо, а не пересказывает правило — оно уже применено.
        "name_source": folder_name_source(titles),
        "root_path": root_path,
        "root_kind": root_kind,
        "full_path": str(Path(root_path) / folder_name),
        # Папка на томе только для чтения не создастся — сказать об этом надо
        # заранее, а не после того, как задача упадёт в очереди.
        "writable": writable,
    }


def episode_target_folder(
    series_id: int, episode: float | None
) -> tuple[LibraryFolder, LibraryRoot] | None:
    """Персональная папка серии, если её назначили.

    Возвращает `None`, когда назначения нет, — это самый частый случай, и
    вызывающий просто идёт обычным путём.
    """
    if episode is None:
        return None

    with session_scope() as session:
        row = session.execute(
            select(EpisodeTarget, LibraryFolder, LibraryRoot)
            .join(LibraryFolder, EpisodeTarget.folder_id == LibraryFolder.id)
            .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
            .where(EpisodeTarget.series_id == series_id, EpisodeTarget.episode == episode)
        ).first()
        if row is None:
            return None
        _, folder, root = row
        session.expunge(folder)
        session.expunge(root)
        return folder, root


def ensure_target_folder(
    series_id: int, episode: float | None = None
) -> tuple[LibraryFolder, LibraryRoot] | None:
    """Папка для загрузки: назначение серии, привязка тайтла либо новая папка.

    Порядок именно такой. Назначение отдельной серии перевешивает общую папку
    тайтла — в этом весь его смысл: серию попросили положить отдельно, и
    батч «все серии» не должен это отменять.

    Тайтла может не быть в библиотеке вовсе — это штатный случай, когда качают
    что-то новое. Тогда создаём папку по названию тайтла в подходящем корне
    (фильмы отдельно от сериалов) и сразу привязываем: дальше всё работает как
    с любой другой папкой, включая маппинг серий и подсчёт наличия.
    """
    override = episode_target_folder(series_id, episode)
    if override is not None:
        return override

    existing = _primary_folder(series_id)
    if existing is not None:
        folder, root = existing
        if not root.is_writable:
            return existing
        return _ensure_season_subfolder(folder, root, series_id)

    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            return None
        titles = dict(series.titles or {})
        # Решение пользователя перевешивает тип из каталога: у тайтла с
        # type=movie и несколькими эпизодами это связка фильмов, которую
        # Radarr фильмом не считает, и класть её надо к сериалам.
        # Одиночный movie — фильм; связка из нескольких — сериал, иначе части
        # Связка фильмов тоже уходит в киношную библиотеку — каждой части
        # своя папка. См. coverage.auto_is_movie и _layout_with_part.
        is_movie, part = _layout_with_part(series, series_id, episode)
        year = series.year

    folder_name = folder_name_for_title(titles, year=year, is_movie=is_movie, part=part)
    if not folder_name:
        log.error("У тайтла %s нет пригодного названия для папки", series_id)
        return None

    root_kind = "movie" if is_movie else "series"
    with session_scope() as session:
        root = session.execute(
            select(LibraryRoot).where(
                LibraryRoot.kind == root_kind, LibraryRoot.is_available.is_(True)
            )
        ).scalars().first()
        if root is None:
            log.error("Нет доступного корня библиотеки для типа %s", root_kind)
            return None
        if not root.is_writable:
            log.error("Корень %s только для чтения — папку не создать", root.path)
            return None
        root_id, root_path = root.id, root.path

    target = Path(root_path) / folder_name
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("Не удалось создать папку %s: %s", target, exc)
        return None

    with session_scope() as session:
        folder = session.execute(
            select(LibraryFolder).where(
                LibraryFolder.root_id == root_id, LibraryFolder.rel_path == folder_name
            )
        ).scalar_one_or_none()
        if folder is None:
            folder = LibraryFolder(
                root_id=root_id,
                rel_path=folder_name,
                name=folder_name,
                season_number=None,
                video_count=0,
            )
            session.add(folder)
            session.flush()

        exists = session.execute(
            select(TitleMapping).where(
                TitleMapping.series_id == series_id, TitleMapping.folder_id == folder.id
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                TitleMapping(
                    series_id=series_id,
                    folder_id=folder.id,
                    source="created",
                    confidence=100.0,
                    matched_on="папка создана при загрузке",
                    is_manual=False,
                    is_primary=True,
                )
            )

    log.info("Создана папка для тайтла %s: %s", series_id, target)
    created = _primary_folder(series_id)
    if created is None:
        return None
    # Папка только что заведена и пуста, поэтому подпапку сезона делаем сразу:
    # разрывать нечего, а раскладка получается такой же, как у Sonarr.
    return _ensure_season_subfolder(created[0], created[1], series_id)


def _existing_video_for_episode(folder_id: int, episode: float) -> str | None:
    """Имя уже лежащего видеофайла этой серии.

    Нужно для субтитров: плеер связывает их с видео по началу имени. Всё, что
    идёт после basename видео, может быть любым — язык, автор, флаги, — но сам
    basename обязан совпадать.

    ⚠️ **У фильма номера серии нет, и поиск по нему не находил ничего.** Тогда
    имя собиралось по канонической схеме, и получалась пара, которая лежит
    рядом, но не связана (случай с сервера, 2026-08-05):

        Chainsaw Man - The Movie - Reze Arc (2025) - [WEBDL-1080p] [h265]….mkv
        Chainsaw Man Movie Reze-hen S01E01 [anime365_Crunchyroll]….ass  ← мимо

    У сериала та же логика срабатывала (номера серий есть у всех файлов), и
    субтитры подхватывались — поэтому беда выглядела выборочной.

    Поэтому если по номеру не нашлось, а видео в папке **ровно одно**, берём
    его: это либо фильм, либо папка с единственной серией, и ошибиться не в
    чем. При нескольких файлах без номеров гадать не станем — лучше
    каноническое имя, чем субтитры, приклеенные к чужой серии.
    """
    with session_scope() as session:
        row = session.execute(
            select(LibraryFile.filename)
            .where(
                LibraryFile.folder_id == folder_id,
                LibraryFile.kind == "video",
                LibraryFile.episode == episode,
            )
            .limit(1)
        ).scalar_one_or_none()
        if row is not None:
            return row

        alone = session.execute(
            select(LibraryFile.filename)
            .where(LibraryFile.folder_id == folder_id, LibraryFile.kind == "video")
            .limit(2)
        ).scalars().all()

    return alone[0] if len(alone) == 1 else None


def _folder_filenames(folder_id: int) -> list[str]:
    with session_scope() as session:
        return [
            row[0]
            for row in session.execute(
                select(LibraryFile.filename).where(LibraryFile.folder_id == folder_id)
            ).all()
        ]


def already_downloaded(translation_id: int) -> bool:
    """Этот перевод уже лежит локально.

    Файлы, скачанные штатным загрузчиком сайта, содержат id перевода в имени —
    сканер их распознаёт, поэтому повторную загрузку можно не делать.
    """
    with session_scope() as session:
        return (
            session.execute(
                select(LibraryFile.id)
                .where(LibraryFile.anime365_translation_id == translation_id)
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )


def existing_audio_path(translation_id: int) -> Path | None:
    """Путь к уже извлечённой дорожке, если она на месте.

    Проверяем по завершённой задаче, а не по имени файла: имя зависит от
    расширения, а оно известно только после разбора источника — то есть уже
    после того, как видео при необходимости скачано. Проверять там было бы
    поздно: самая дорогая часть работы к тому моменту сделана.

    Файл на диске проверяем обязательно: запись о задаче могла пережить
    удаление файла руками, и тогда «уже скачано» было бы неправдой.

    ⚠️ Для японской дорожки совпадение ищется по СЕРИИ, а не по переводу.
    Причина в устройстве источника: перевод с субтитрами — это то же видео с
    той же японской дорожкой, что и равка, и что другой перевод с другими
    субтитрами. Извлекать её по разу с каждого значило бы разложить рядом
    несколько одинаковых файлов.

    Для озвучек так делать нельзя: RU и EN у разных студий разные, и вторая
    озвучка той же серии — это другая дорожка, а не дубль.
    """
    with session_scope() as session:
        source = session.execute(
            select(DownloadTask.series_id, DownloadTask.episode, DownloadTask.lang)
            .where(DownloadTask.translation_id == translation_id)
            .limit(1)
        ).first()

        condition = DownloadTask.translation_id == translation_id
        if source is not None:
            series_id, episode, lang = source
            if lang == "ja" and series_id is not None and episode is not None:
                condition = (
                    (DownloadTask.series_id == series_id)
                    & (DownloadTask.episode == episode)
                    & (DownloadTask.lang == "ja")
                )

        rows = (
            session.execute(
                select(DownloadTask)
                .where(
                    condition,
                    DownloadTask.kind == "audio",
                    DownloadTask.status.in_([TaskStatus.DONE, TaskStatus.ALREADY_EXISTS]),
                )
                .order_by(DownloadTask.id.desc())
            )
            .scalars()
            .all()
        )

    for task in rows:
        if not task.target_path:
            continue
        path = Path(task.target_path)
        if path.is_file():
            return path
    return None


def enqueue(
    translation_id: int,
    kind: str,
    requested_height: int | None = None,
    user_id: int = 1,
) -> dict[str, Any]:
    """Ставит задачу в очередь. Идемпотентно по переводу, типу и качеству."""
    if kind not in TASK_KINDS:
        raise QueueError(f"Неизвестный тип задачи: {kind!r}")

    with session_scope() as session:
        translation = session.get(Translation, translation_id)
        if translation is None:
            raise QueueError(f"Перевод {translation_id} не найден в кэше")

        series = session.get(Series, translation.series_id)
        series_title = (series.titles or {}).get("romaji") or (series.title if series else "")

        # Дорожка этого перевода уже извлечена и лежит на месте. Второй раз
        # она дала бы файл с тем же именем — то есть дубль рядом.
        if kind == "audio":
            existing_audio = existing_audio_path(translation_id)
            if existing_audio is not None:
                return {
                    "id": None,
                    "status": TaskStatus.ALREADY_EXISTS,
                    "created": False,
                    "already_exists": True,
                    "path": str(existing_audio),
                }

        # Уже стоит в очереди или качается — второй раз не ставим.
        existing = session.execute(
            select(DownloadTask).where(
                DownloadTask.translation_id == translation_id,
                DownloadTask.kind == kind,
                DownloadTask.active_key == ACTIVE_KEY,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"id": existing.id, "status": existing.status, "created": False}

        task = DownloadTask(
            translation_id=translation_id,
            series_id=translation.series_id,
            episode=None,
            kind=kind,
            requested_height=requested_height or settings.get_int("preferred_height"),
            series_title=series_title,
            authors=translation.authors_summary or "",
            lang=translation.type_lang,
            type_kind=translation.type_kind,
            created_by_user_id=user_id,
            active_key=ACTIVE_KEY,
            # В конец очереди: новая задача не должна лезть вперёд того, что
            # человек уже поставил и, возможно, успел упорядочить.
            position=_next_position(session),
        )

        # Номер серии берём из кэша эпизодов.
        from app.models.catalog import Episode

        if translation.episode_id:
            episode = session.get(Episode, translation.episode_id)
            if episode is not None:
                task.episode = episode.episode_num

        session.add(task)
        session.flush()
        return {"id": task.id, "status": task.status, "created": True}


def _write_progress(task_id: int, progress) -> None:
    """Сохраняет прогресс — и никогда не роняет из-за этого загрузку.

    ⚠️ Раньше исключение отсюда летело наружу и убивало задачу: в логах
    пользователя лежит «Задача 69 упала (database is locked), повтор через
    120 с» — при том что файл в этот момент качался нормально. Прогресс это
    украшение: не записался — следующая попытка через секунду покажет то же
    самое, а недокачанное продолжится с того же места.

    Ошибку всё-таки логируем: молчать нельзя, по ней видно, что база под
    нагрузкой. Уровень предупреждения, а не ошибки — сама по себе она ничего
    не ломает.
    """
    try:
        with session_scope() as session:
            task = session.get(DownloadTask, task_id)
            if task is not None:
                task.bytes_done = progress.bytes_done
                task.bytes_total = progress.bytes_total
                task.speed_bps = progress.speed_bps
    except SQLAlchemyError as exc:
        log.warning("Прогресс задачи %s не записан: %s", task_id, str(exc).splitlines()[0])


def _finish(task_id: int, status: str, error: str | None = None) -> None:
    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            return
        task.status = status
        task.last_error = error
        task.finished_at = datetime.utcnow()
        # Снимаем задачу с уникального ключа, чтобы её можно было поставить заново.
        task.active_key = task.id

    download_queue.forget_cancelled(task_id)
    bus.publish("download.status", task_id=task_id, status=str(status), error=error)


def _next_position(session) -> int:
    """Номер за последней задачей в очереди.

    Считается по всем задачам, а не только по ждущим: завершённые сохраняют
    свою позицию, и переиспользовать её значило бы поставить новую задачу в
    середину списка.
    """
    current = session.execute(
        select(func.max(func.coalesce(DownloadTask.position, DownloadTask.id)))
    ).scalar()
    return int(current or 0) + 1


def _first_position(session) -> int:
    """Номер перед первой задачей в очереди.

    Позиции могут уйти в минус, и это нормально: порядок задаётся сравнением,
    а не абсолютным значением. Иначе пришлось бы перенумеровывать всю очередь
    на каждый подъём наверх.
    """
    current = session.execute(
        select(func.min(func.coalesce(DownloadTask.position, DownloadTask.id)))
    ).scalar()
    return int(current or 1) - 1


def _fail_with_backoff(task_id: int, error: str) -> None:
    """Помечает падение и назначает время повтора."""
    max_attempts = settings.get_int("max_attempts")
    base = settings.get_int("retry_backoff_base")

    with session_scope() as session:
        task = session.get(DownloadTask, task_id)
        if task is None:
            return
        task.attempts += 1
        task.last_error = error[:2000]

        if task.attempts >= max_attempts:
            task.status = TaskStatus.FAILED
            task.finished_at = datetime.utcnow()
            task.active_key = task.id
            log.error("Задача %s исчерпала попытки: %s", task_id, error)
            return

        delay = base * (2 ** (task.attempts - 1))
        task.status = TaskStatus.QUEUED
        task.retry_after = datetime.utcnow() + timedelta(seconds=delay)
        # Упавшая задача уходит в конец очереди. Иначе она осталась бы первой и
        # упёрлась бы в ту же ошибку раньше всего остального — а причина у
        # ошибок часто постоянная. Кому нужно быстрее, тот перетащит её обратно
        # наверх.
        task.position = _next_position(session)
        log.warning("Задача %s упала (%s), повтор через %d с", task_id, error, delay)

    bus.publish("download.status", task_id=task_id, status="retrying", error=error)


class DownloadQueue:
    def __init__(self) -> None:
        self._workers: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._paused = False
        self._cancelled: set[int] = set()
        # Цикл, в котором живут воркеры. Нужен, чтобы попросить перезапуск из
        # синхронного обработчика: FastAPI выполняет их в отдельном потоке, где
        # работающего цикла нет вовсе.
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ #
    # Управление
    # ------------------------------------------------------------------ #

    @property
    def paused(self) -> bool:
        return self._paused

    def pause_all(self) -> None:
        self._paused = True

    def resume_all(self) -> None:
        self._paused = False

    def cancel(self, task_id: int) -> None:
        self._cancelled.add(task_id)

    def forget_cancelled(self, task_id: int) -> None:
        """Убирает отметку отмены у завершённой задачи.

        Без этого множество растёт всю жизнь процесса: отметка снимается только
        в той ветке, которая её отработала, а задача может успеть завершиться
        раньше, чем воркер до неё дойдёт.
        """
        self._cancelled.discard(task_id)

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        try:
            self._loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            # Запуск вне цикла бывает в тестах — там перезапускать нечего.
            self._loop = None

        count = settings.get_int("max_concurrent_downloads")
        # 0 означает «без ограничения», но бесконечных воркеров не бывает:
        # берём разумный потолок, иначе сотня параллельных загрузок положит сеть.
        workers = 8 if count == 0 else count

        self._recover_interrupted()

        self._stop.clear()
        for index in range(workers):
            self._workers.append(asyncio.create_task(self._worker(index)))

        # Постобработка идёт своим воркером и в одном экземпляре: параллельное
        # перемещение файлов на один диск только отнимает у него головки, а
        # выигрыша не даёт.
        self._workers.append(asyncio.create_task(self._postprocess_worker()))
        log.info("Очередь загрузок запущена: воркеров %d + постобработка", workers)

    async def stop(self) -> None:
        self._stop.set()
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()

    def worker_count(self) -> int:
        """Сколько воркеров загрузки сейчас живо. Постобработка не в счёт."""
        # Последний в списке — воркер постобработки, он всегда один.
        return max(0, len(self._workers) - 1)

    def request_restart(self) -> bool:
        """Просит перезапустить воркеров. Безопасно из синхронного кода.

        Возвращает False, если цикла нет — тогда перезапускать нечего и звать
        было неоткуда.
        """
        if self._loop is None or self._loop.is_closed():
            return False
        asyncio.run_coroutine_threadsafe(self.restart(), self._loop)
        return True

    async def restart(self) -> None:
        """Перезапускает воркеров под текущее значение настройки.

        Нужно потому, что число воркеров читается один раз при старте: без
        этого правка «параллельных загрузок» в интерфейсе не делала ничего до
        перезапуска контейнера, и человек видел сохранённое значение, которое
        ни на что не влияет.

        Активные загрузки при этом не теряются: воркер снимается между
        задачами, а незавершённые задачи возвращаются в очередь тем же
        механизмом, что и после перезапуска контейнера, — с докачкой по Range.
        """
        await self.stop()
        self.start()

    # ------------------------------------------------------------------ #
    # Воркер
    # ------------------------------------------------------------------ #

    async def _worker(self, index: int) -> None:
        while not self._stop.is_set():
            if self._paused:
                await asyncio.sleep(2)
                continue

            task_id = self._claim_next()
            if task_id is None:
                await asyncio.sleep(3)
                continue

            try:
                await self._process(task_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Воркер %d: задача %s упала", index, task_id)
                _fail_with_backoff(task_id, str(exc))

    def _claim_next(self) -> int | None:
        """Забирает следующую задачу, помечая её как выполняющуюся."""
        now = datetime.utcnow()
        with session_scope() as session:
            task = session.execute(
                select(DownloadTask)
                .where(
                    DownloadTask.status == TaskStatus.QUEUED,
                    (DownloadTask.retry_after.is_(None)) | (DownloadTask.retry_after <= now),
                )
                # Строго по позиции — тому порядку, который пользователь видит
                # в списке и меняет перетаскиванием. У записей без позиции
                # (заведены до появления поля) её роль играет id.
                .order_by(func.coalesce(DownloadTask.position, DownloadTask.id))
                .limit(1)
            ).scalar_one_or_none()
            if task is None:
                return None
            task.status = TaskStatus.DOWNLOADING
            return task.id

    async def _process(self, task_id: int) -> None:
        with session_scope() as session:
            task = session.get(DownloadTask, task_id)
            if task is None:
                return
            snapshot = {
                "translation_id": task.translation_id,
                "series_id": task.series_id,
                "episode": task.episode,
                "kind": task.kind,
                "requested_height": task.requested_height,
                "authors": task.authors,
                "lang": task.lang,
                "type_kind": task.type_kind,
                "series_title": task.series_title,
            }

        if snapshot["kind"] == "audio":
            # У фонотеки своя логика: файл может вообще не качаться, а цель
            # лежит вне библиотеки. Смешивать её с ветками видео и субтитров
            # значило бы обвесить общий путь условиями на каждом шаге.
            await self._process_audio(task_id, snapshot)
            return

        if already_downloaded(snapshot["translation_id"]):
            log.info("Перевод %s уже есть локально", snapshot["translation_id"])
            _finish(task_id, TaskStatus.ALREADY_EXISTS)
            return

        # Папки может не быть вовсе — тогда создаём её по названию тайтла.
        # Номер серии обязателен: без него персональное назначение не найдётся,
        # и файл уедет в общую папку вопреки тому, что попросил пользователь.
        target = ensure_target_folder(snapshot["series_id"], snapshot.get("episode"))
        if target is None:
            # Называем настоящую причину: «не вышло создать папку» заставляло
            # гадать между ненастроенным корнем, режимом только для чтения и
            # отвалившимся томом.
            with session_scope() as session:
                series = session.get(Series, snapshot["series_id"])
                is_movie = _resolve_layout(series, snapshot["series_id"]) if series else False
            _finish(task_id, TaskStatus.FAILED, why_no_folder(snapshot["series_id"], is_movie))
            return

        folder, root = target
        if not root.is_writable:
            _finish(
                task_id,
                TaskStatus.FAILED,
                f"Папка {root.path} смонтирована только для чтения — загрузка невозможна",
            )
            return

        tmp_dir = Path(settings.get_str("download_tmp"))
        # Проверяем оба тома: файл скачивается во временную папку и переезжает
        # в библиотеку, а в compose это разные монтирования. Проверка только
        # временной папки обнаруживала бы нехватку места уже после загрузки.
        problem = check_free_space(
            tmp_dir,
            Path(root.path) / folder.rel_path,
            settings.get_int("min_free_space_gb") * (1 << 30),
        )
        if problem:
            _fail_with_backoff(task_id, problem)
            return

        async with Downloader() as downloader:
            links = await downloader.resolve_links(snapshot["translation_id"])

            if snapshot["kind"] == "subtitle":
                url = links.get("subtitles_url")
                if not url:
                    # У перевода с субтитрами это означает, что они вшиты в видео.
                    _finish(
                        task_id,
                        TaskStatus.FAILED,
                        "У этого перевода субтитры вшиты в видео, отдельного файла нет",
                    )
                    return
                # ⚠️ Разрешение субтитрам нужно, хотя своего у них нет.
                #
                # Имя субтитров обязано совпасть с именем видео целиком, иначе
                # ни Jellyfin, ни плеер их не подхватят. У видео в имени стоит
                # `WEBDL-1080p`, и если у субтитров оставить голое `WEBDL`,
                # получается пара, которая рядом лежит, но не связана:
                #
                #     … [anime365_Japanese_(BD)] WEBDL-1080p.ja.mp4
                #     … [anime365_Japanese_(BD)] WEBDL.ja.ass      ← мимо
                #
                # Поэтому берём ту же ступень, что взяло бы видео: обе задачи
                # рождаются из одного перевода с одним `requested_height`, так
                # что число совпадёт. Ссылка при этом не нужна — только высота.
                _, height = pick_download_url(links, snapshot["requested_height"] or 1080)
                extension = SUBTITLE_EXTENSION
            else:
                url, height = pick_download_url(links, snapshot["requested_height"] or 1080)
                if not url:
                    _finish(task_id, TaskStatus.FAILED, "Нет доступных ссылок для скачивания")
                    return
                extension = VIDEO_EXTENSION

            filename = self._build_name(snapshot, folder, height or 0, extension)
            tmp_path = tmp_dir / f"a365-{task_id}{extension}"

            with session_scope() as session:
                db_task = session.get(DownloadTask, task_id)
                if db_task is not None:
                    db_task.resolved_height = height
                    db_task.tmp_path = str(tmp_path)
                    db_task.target_path = str(Path(root.path) / folder.rel_path / filename)

            last_publish = 0.0

            def on_progress(progress) -> None:
                nonlocal last_publish
                # ⚠️ Раз в секунду, а не на каждый чанк. Чанк — мегабайт, и на
                # быстром канале это были десятки транзакций в секунду на файл.
                # Каждая берёт writer-lock, а SQLite допускает одного писателя:
                # так загрузки конкурировали и друг с другом, и со сканами, и
                # получали «database is locked».
                #
                # Событие в интерфейс шло раз в секунду и раньше — то есть
                # тормозящую запись делали чаще, чем то, ради чего она нужна.
                now = monotonic()
                if now - last_publish < 1.0:
                    return
                last_publish = now

                _write_progress(task_id, progress)
                bus.publish(
                    "download.progress",
                    task_id=task_id,
                    bytes_done=progress.bytes_done,
                    bytes_total=progress.bytes_total,
                    speed_bps=round(progress.speed_bps),
                )

            def should_stop() -> bool:
                return task_id in self._cancelled or self._paused or self._stop.is_set()

            try:
                expect = "subtitle" if snapshot["kind"] == "subtitle" else "video"
                await downloader.fetch(url, tmp_path, on_progress, should_stop, expect=expect)
            except DownloadCancelled:
                # Проверяем ДО снятия отметки: иначе отмена всегда выглядела бы паузой.
                was_cancelled = task_id in self._cancelled
                self._cancelled.discard(task_id)

                if was_cancelled:
                    # Недокачанный кусок удаляем: возобновлять отменённое незачем.
                    tmp_path.with_suffix(tmp_path.suffix + ".part").unlink(missing_ok=True)
                    _finish(task_id, TaskStatus.CANCELLED)
                else:
                    # Пауза: .part остаётся, докачаем с того же места.
                    with session_scope() as session:
                        db_task = session.get(DownloadTask, task_id)
                        if db_task is not None:
                            db_task.status = TaskStatus.PAUSED
                return
            except SubscriptionRequired as exc:
                _finish(task_id, TaskStatus.FAILED, str(exc))
                return

        # Загрузка на этом закончена. Перемещение делает отдельный воркер:
        # между дисками shutil.move копирует байты, и держать на этом воркер
        # загрузки означало бы простой сети на всё время копирования.
        # Всё нужное для перемещения уже лежит в задаче (tmp_path, target_path).
        with session_scope() as session:
            db_task = session.get(DownloadTask, task_id)
            if db_task is not None:
                db_task.status = TaskStatus.DOWNLOADED
                db_task.speed_bps = 0.0
        bus.publish("download.status", task_id=task_id, status=TaskStatus.DOWNLOADED)

    async def _process_audio(self, task_id: int, snapshot: dict) -> None:
        """Извлечение звуковой дорожки.

        Два пути, различие по решению пользователя:
          * видео этой серии уже лежит локально — берём дорожку прямо из него,
            сам файл не трогаем;
          * видео нет — качаем во временную папку, вырезаем звук и **удаляем
            скачанное**: оно нужно было только как источник.

        Результат кладём во временную папку, а переносит его общий воркер
        постобработки. Так частично записанный файл не появится в фонотеке.
        """
        from app.services.audio import (
            AudioError,
            extract_audio,
            ffmpeg_available,
            pick_stream,
            probe_audio_streams,
        )

        if not ffmpeg_available():
            _finish(
                task_id,
                TaskStatus.FAILED,
                "В образе нет ffmpeg — извлечение звука недоступно. "
                "Пересоберите образ или отключите загрузку дорожек",
            )
            return

        # Та же проверка, что и на постановке: между ними файл мог появиться —
        # например, задачу поставили дважды, пока первая ещё шла.
        already = existing_audio_path(snapshot["translation_id"])
        if already is not None:
            log.info("Дорожка уже извлечена: %s", already.name)
            _finish(task_id, TaskStatus.ALREADY_EXISTS)
            return

        target_dir = _audio_target_folder(snapshot["series_id"])
        if target_dir is None:
            _finish(task_id, TaskStatus.FAILED, "Не удалось определить папку в корне фонотеки")
            return

        tmp_dir = Path(settings.get_str("download_tmp"))
        problem = check_free_space(
            tmp_dir, target_dir, settings.get_int("min_free_space_gb") * (1 << 30)
        )
        if problem:
            _fail_with_backoff(task_id, problem)
            return

        episode = snapshot["episode"] or 1
        source = _existing_video_path_for_episode(snapshot["series_id"], episode)
        downloaded_here = source is None
        tmp_video: Path | None = None

        try:
            if source is None:
                source = tmp_video = await self._download_source_video(task_id, snapshot, tmp_dir)
                if source is None:
                    return
            else:
                log.info("Беру дорожку из уже скачанного %s", source.name)

            try:
                streams = await probe_audio_streams(source)
            except AudioError as exc:
                _fail_with_backoff(task_id, str(exc))
                return

            stream = pick_stream(streams, snapshot["lang"])
            if stream is None:
                _finish(task_id, TaskStatus.FAILED, f"В файле {source.name} нет звуковых дорожек")
                return

            if len(streams) > 1 and stream.lang != snapshot["lang"]:
                # Не ошибка, но пользователь должен знать: язык дорожки может
                # не совпасть с языком перевода, если разметка в файле неполная.
                log.warning(
                    "Дорожка выбрана без совпадения по языку: нужен %s, взята %s",
                    snapshot["lang"],
                    stream.lang or "без метки",
                )

            ctx = NamingContext(
                show_title=snapshot["series_title"] or "Unknown",
                season=None,
                episode=episode,
                episode_title=None,
                authors=snapshot["authors"],
                height=0,
                lang=snapshot["lang"] or stream.lang or "ja",
                kind=snapshot["type_kind"] or "raw",
                extension=stream.extension,
            )
            filename = audio_name(ctx)

            # Последний рубеж: запись о прошлой задаче могли удалить, а файл
            # остался. Перезаписывать его молча нельзя.
            if (target_dir / filename).is_file():
                log.info("Дорожка уже лежит в фонотеке: %s", filename)
                _finish(task_id, TaskStatus.ALREADY_EXISTS)
                return

            tmp_audio = tmp_dir / f"a365-audio-{task_id}{stream.extension}"

            try:
                await extract_audio(source, stream, tmp_audio)
            except AudioError as exc:
                _fail_with_backoff(task_id, str(exc))
                return

            with session_scope() as session:
                db_task = session.get(DownloadTask, task_id)
                if db_task is not None:
                    db_task.tmp_path = str(tmp_audio)
                    db_task.target_path = str(target_dir / filename)
                    db_task.status = TaskStatus.DOWNLOADED
                    db_task.speed_bps = 0.0
            bus.publish("download.status", task_id=task_id, status=TaskStatus.DOWNLOADED)
            log.info("Дорожка извлечена: %s (%s)", filename, stream.codec)

        finally:
            # Скачанное ради звука видео в библиотеке не нужно — по решению
            # пользователя оно идёт в мусор. Уже лежавшее не трогаем никогда.
            if downloaded_here and tmp_video is not None:
                tmp_video.unlink(missing_ok=True)
                tmp_video.with_suffix(tmp_video.suffix + ".part").unlink(missing_ok=True)

    async def _download_source_video(
        self, task_id: int, snapshot: dict, tmp_dir: Path
    ) -> Path | None:
        """Качает видео во временную папку — только как источник звука.

        В библиотеку оно не попадёт, поэтому имя техническое и качество берём
        минимальное из доступных: звуковая дорожка от разрешения не зависит,
        а лишние сотни мегабайт качать незачем.
        """
        async with Downloader() as downloader:
            try:
                links = await downloader.resolve_links(snapshot["translation_id"])
            except SubscriptionRequired as exc:
                _finish(task_id, TaskStatus.FAILED, str(exc))
                return None

            heights = sorted(links.get("heights") or [])
            url, _ = pick_download_url(links, heights[0] if heights else 360)
            if not url:
                _finish(task_id, TaskStatus.FAILED, "Нет доступных ссылок для скачивания")
                return None

            tmp_video = tmp_dir / f"a365-src-{task_id}{VIDEO_EXTENSION}"
            last_write = 0.0

            def on_progress(progress) -> None:
                # Та же причина, что и у обычной загрузки: чаще раза в секунду
                # писать прогресс в базу незачем, а вред от этого реальный.
                nonlocal last_write
                now = monotonic()
                if now - last_write < 1.0:
                    return
                last_write = now
                _write_progress(task_id, progress)

            def should_stop() -> bool:
                return task_id in self._cancelled or self._paused or self._stop.is_set()

            try:
                await downloader.fetch(url, tmp_video, on_progress, should_stop, expect="video")
            except DownloadCancelled:
                was_cancelled = task_id in self._cancelled
                self._cancelled.discard(task_id)
                tmp_video.unlink(missing_ok=True)
                tmp_video.with_suffix(tmp_video.suffix + ".part").unlink(missing_ok=True)
                _finish(task_id, TaskStatus.CANCELLED if was_cancelled else TaskStatus.PAUSED)
                return None
            except Anime365Error as exc:
                _fail_with_backoff(task_id, str(exc))
                return None

            return tmp_video

    def _build_name(
        self, snapshot: dict, folder: LibraryFolder, height: int, extension: str
    ) -> str:
        episode = snapshot["episode"] or 1
        existing_video = None
        if snapshot["kind"] == "subtitle":
            existing_video = _existing_video_for_episode(folder.id, episode)

        neighbour = None
        if existing_video is None:
            from app.services.filename_rules import get_rules

            rules = get_rules()
            neighbour = pick_neighbour(_folder_filenames(folder.id), set(rules.video_ext))

        ctx = NamingContext(
            show_title=snapshot["series_title"] or "Unknown",
            season=folder.season_number,
            episode=episode,
            episode_title=None,
            authors=snapshot["authors"],
            height=height,
            lang=snapshot["lang"] or "ja",
            kind=snapshot["type_kind"] or "raw",
            extension=extension,
        )
        name = build_target_name(ctx, neighbour=neighbour, existing_video=existing_video)
        return unique_path(folder.rel_path, name, set(_folder_filenames(folder.id)))

    @staticmethod
    def _recover_interrupted() -> None:
        """Возвращает в работу задачи, застрявшие из-за остановки процесса.

        Статусы `downloading` и `postprocessing` означают «этим прямо сейчас
        занят воркер». После падения или рестарта контейнера воркеров нет, а
        метки остаются — и такие задачи не забирал никто: `_claim_next` берёт
        только `queued`. То есть загрузка, прерванная перезапуском, зависала
        навсегда, хотя обещано, что она продолжится с того же места.

        Недокачанный `.part` при этом сохраняется, поэтому возврат в очередь
        означает докачку по Range, а не загрузку заново.
        """
        with session_scope() as session:
            downloading = session.execute(
                select(DownloadTask).where(DownloadTask.status == TaskStatus.DOWNLOADING)
            ).scalars().all()
            for task in downloading:
                task.status = TaskStatus.QUEUED
                task.speed_bps = 0.0

            postprocessing = session.execute(
                select(DownloadTask).where(DownloadTask.status == TaskStatus.POSTPROCESSING)
            ).scalars().all()
            for task in postprocessing:
                # Файл уже скачан целиком — возвращаем на перемещение, не на загрузку.
                task.status = TaskStatus.DOWNLOADED

        if downloading or postprocessing:
            log.info(
                "Возобновлено после перезапуска: загрузок %d, перемещений %d",
                len(downloading),
                len(postprocessing),
            )

    def _claim_next_postprocess(self) -> tuple[int, Path, Path] | None:
        """Забирает скачанную задачу под перемещение.

        Отдельная очередь и отдельная метка статуса нужны, чтобы перемещение
        между дисками не занимало воркер загрузки: `shutil.move` там копирует
        байты, и на большом файле это минуты простоя сети.
        """
        with session_scope() as session:
            task = session.execute(
                select(DownloadTask)
                .where(DownloadTask.status == TaskStatus.DOWNLOADED)
                .order_by(DownloadTask.updated_at)
                .limit(1)
            ).scalar_one_or_none()
            if task is None:
                return None

            if task.id in self._cancelled:
                # Отмена задачи, ждущей перемещения. Без этой ветки файл всё
                # равно уезжал в библиотеку, хотя API уже ответил «отменено».
                self._cancelled.discard(task.id)
                if task.tmp_path:
                    Path(task.tmp_path).unlink(missing_ok=True)
                task.status = TaskStatus.CANCELLED
                task.finished_at = datetime.utcnow()
                task.active_key = task.id
                log.info("Задача %s отменена до перемещения", task.id)
                return None

            if not task.tmp_path or not task.target_path:
                # Такого быть не должно: пути пишутся до начала загрузки.
                # Но если задача пережила рестарт в странном состоянии, лучше
                # честно её провалить, чем молча оставить висеть в очереди.
                task.status = TaskStatus.FAILED
                task.last_error = "Потеряны пути файла — перезапустите загрузку"
                return None
            task.status = TaskStatus.POSTPROCESSING
            return task.id, Path(task.tmp_path), Path(task.target_path)

    async def _postprocess_worker(self) -> None:
        while not self._stop.is_set():
            if self._paused:
                # «Пауза» для пользователя означает «ничего не происходит».
                # Скачанное подождёт: файл целый, метка downloaded сохранена,
                # после возобновления он переедет.
                await asyncio.sleep(2)
                continue

            claimed = self._claim_next_postprocess()
            if claimed is None:
                await asyncio.sleep(2)
                continue

            task_id, tmp_path, target_path = claimed
            bus.publish("download.status", task_id=task_id, status=TaskStatus.POSTPROCESSING)
            try:
                await self._postprocess(task_id, tmp_path, target_path.parent, target_path.name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Постобработка задачи %s упала", task_id)
                _fail_with_backoff(task_id, str(exc))

    async def _postprocess(
        self, task_id: int, tmp_path: Path, target_dir: Path, filename: str
    ) -> None:
        """Перемещение в библиотеку и выставление прав."""
        try:
            # Последний рубеж перед записью. Имя автора перевода приходит из
            # внешнего API и попадает в имя файла, а корень и rel_path — из БД:
            # сверяем итог с настроенным корнем, а не доверяем цепочке склеек.
            destination = resolve_within(target_dir, filename)
        except UnsafePathError as exc:
            _fail_with_backoff(task_id, str(exc))
            log.error("Отклонена запись за пределы библиотеки: %s", exc)
            return

        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            # shutil.move переживает перемещение между дисками, os.rename — нет,
            # а временная папка вполне может лежать не там же, где библиотека.
            await asyncio.to_thread(shutil.move, str(tmp_path), str(destination))
            await asyncio.to_thread(self._apply_ownership, destination)
        except OSError as exc:
            _fail_with_backoff(task_id, f"Не удалось переместить файл: {exc}")
            return

        _finish(task_id, TaskStatus.DONE)
        log.info("Готово: %s", destination.name)

        # Просим Jellyfin пересканировать: иначе серия появится у него только
        # по его собственному расписанию. Вызов схлопывается — батч из полусотни
        # серий даст одно обновление, а не полсотни.
        from app.services.jellyfin import jellyfin

        jellyfin.schedule()

    @staticmethod
    def _apply_ownership(path: Path) -> None:
        """Права на созданный файл, чтобы Jellyfin мог его прочитать."""
        import os

        try:
            umask = int(settings.get_str("umask"), 8)
            os.chmod(path, 0o666 & ~umask)
        except (OSError, ValueError) as exc:
            log.debug("Не удалось выставить права на %s: %s", path, exc)

        if hasattr(os, "chown"):
            try:
                os.chown(path, settings.get_int("puid"), settings.get_int("pgid"))
            except OSError as exc:
                # В контейнере без CAP_CHOWN это ожидаемо, если файл и так наш.
                log.debug("Не удалось сменить владельца %s: %s", path, exc)


download_queue = DownloadQueue()


def _existing_video_path_for_episode(series_id: int, episode: float) -> Path | None:
    """Полный путь к уже лежащему видео этой серии, если оно есть.

    Нужен фонотеке: по решению пользователя из уже скачанного видео дорожка
    извлекается на месте, а само видео остаётся нетронутым. Скачивать его
    заново ради звука было бы расточительно.
    """
    with session_scope() as session:
        row = session.execute(
            select(LibraryFile.filename, LibraryFolder.rel_path, LibraryRoot.path)
            .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
            .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
            .join(EpisodeMapping, EpisodeMapping.file_id == LibraryFile.id)
            .where(
                EpisodeMapping.series_id == series_id,
                EpisodeMapping.episode == episode,
                EpisodeMapping.role == "video",
                LibraryFile.excluded_reason.is_(None),
            )
            .limit(1)
        ).first()

    if row is None:
        return None
    filename, rel_path, root_path = row
    candidate = Path(root_path) / rel_path / filename
    return candidate if candidate.is_file() else None


def _audio_target_folder(series_id: int) -> Path | None:
    """Папка тайтла внутри корня фонотеки.

    Корень свой, отдельный от библиотеки: Jellyfin индексировал бы звуковые
    файлы как видео без картинки. Имя папки берём тем же правилом, что и для
    библиотеки, — так фонотека и библиотека выглядят одинаково.
    """
    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            return None
        titles = dict(series.titles or {})
        year = series.year
        # Одиночный movie — фильм; связка из нескольких — сериал, иначе части
        # Связка фильмов тоже уходит в киношную библиотеку — каждой части
        # своя папка. См. coverage.auto_is_movie и _layout_with_part.
        is_movie = _resolve_layout(series, series_id)

    # Номер части здесь намеренно не нужен: в имени звукового файла номер серии
    # уже есть, коллизии нет, а дробить фонотеку по частям значило бы разложить
    # два файла по двум папкам без всякой пользы.
    folder_name = folder_name_for_title(titles, year=year, is_movie=is_movie)
    if not folder_name:
        return None

    root = Path(settings.get_str("audio_root"))
    try:
        return resolve_within(root, folder_name)
    except UnsafePathError as exc:
        log.error("Имя папки фонотеки вышло за корень: %s", exc)
        return None
