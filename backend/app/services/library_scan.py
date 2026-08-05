"""Сканирование локальной библиотеки.

Структура, на которую рассчитан обход (подтверждена реальной библиотекой):

    <корень>/<Название тайтла>/                 tvshow.nfo, постеры
    <корень>/<Название тайтла>/Season N/        season.nfo, серии, сайдкар-субтитры
    <корень>/<Название тайтла>/Specials/        спешлы
    <корень>/<Название тайтла>/<файлы>          сезонных подпапок может не быть вовсе

Сканер только читает. Библиотеку штатно монтируют в режиме только для чтения на
время первых прогонов, поэтому доступность записи проверяется отдельно и явно:
загрузки при этом недоступны, но каталог, маппинг и UI работают полностью.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from app.services.filename_rules import RuleSet, extract_season, get_rules, parse_filename
from app.services.nfo_parser import NfoData, parse_nfo

log = logging.getLogger(__name__)

#: Версия разбора файлов и папок.
#:
#: ⚠️ Поднимать при **любом** изменении того, как разбираются имена, .nfo или
#: номера серий. Инкрементальный скан пропускает папку, у которой совпали mtime
#: и число файлов, — и это правильно, пока правила разбора неизменны. Но стоит
#: правилам поменяться, как в базе навсегда остаётся результат старого разбора:
#: на диске-то ничего не менялось.
#:
#: Так и вышло на живом сервере. Правка «единственное видео в папке фильма —
#: это первая серия» уехала в образ, а `Fate+stay night - Heaven's Feel II`
#: продолжал показывать «ни видео, ни субтитров»: папку не трогали с прошлого
#: скана, и её файлы просто не перечитывались. Сверочный полный проход починил
#: бы это через сутки, но узнать причину по симптому невозможно.
#:
#: При несовпадении версии корень сканируется целиком, будто впервые.
PARSER_REVISION = 2

# Папки, которые не являются тайтлами: служебное и временное.
SKIP_DIR_NAMES = frozenset({"tmp", ".tmp", "@eaDir", ".@__thumb", "lost+found", ".git"})

# Имена подпапок, означающие спешлы. Номер сезона у них — 0.
SPECIALS_NAMES = frozenset({"specials", "special", "extras", "ova", "ovas"})

# Какой .nfo описывает саму папку, если их рядом лежит несколько.
#
# В корне фильмов у 42 папок из 58 лежат и `movie.nfo`, и `tvshow.nfo` — след
# того, что когда-то эти папки сканировались как сериальная библиотека. Данные в
# них разные: `title` расходится у 35 папок из 42 (у «Look Back» в `tvshow.nfo`
# записан «Kekkai Sensen & Beyond», у «Chainsaw Man — Reze» — «Arc the Lad»),
# и правильная сторона — всегда `movie.nfo`.
#
# Раньше побеждал не тот файл, причём случайно: записи перебирались по алфавиту,
# и `tvshow.nfo` затирал разобранный ранее `movie.nfo`. Поэтому выбор делается
# явным приоритетом, а не порядком обхода.
NFO_PRIORITY: dict[str, dict[str, int]] = {
    "movie": {"movie": 3, "season": 2, "tvshow": 1},
    "series": {"season": 3, "tvshow": 2, "movie": 1},
}


@dataclass
class ScannedFile:
    filename: str
    kind: str  # video | subtitle | metadata
    size_bytes: int
    mtime: float
    lang: str | None = None
    season_number: int | None = None
    episode: float | None = None
    episode_end: float | None = None
    matched_rule_id: str | None = None
    release_group: str | None = None
    anime365_translation_id: int | None = None
    excluded_reason: str | None = None
    streams: dict[str, Any] | None = None

    @property
    def is_unmatched(self) -> bool:
        # Метаданные и исключённые файлы «нераспознанными» не считаются:
        # они и не должны попадать в маппинг серий.
        if self.kind == "metadata" or self.excluded_reason:
            return False
        return self.episode is None


@dataclass
class ScannedFolder:
    rel_path: str
    name: str
    season_number: int | None
    nfo: NfoData | None
    size_bytes: int = 0
    mtime: float | None = None
    files: list[ScannedFile] = field(default_factory=list)
    children: list[ScannedFolder] = field(default_factory=list)
    # Содержимое совпало с тем, что уже лежит в БД: файлы не разбирались повторно,
    # и переписывать их записи не нужно. Дети при этом всё равно обойдены —
    # изменение внутри подпапки не меняет mtime родителя.
    unchanged: bool = False
    # Папку не удалось прочитать (права, отвалившееся монтирование). Пустой
    # список файлов здесь означает «неизвестно», а не «пусто», поэтому такое
    # поддерево нельзя вычищать из БД как исчезнувшее.
    read_failed: bool = False

    @property
    def video_count(self) -> int:
        return sum(1 for f in self.files if f.kind == "video" and not f.excluded_reason)


@dataclass(frozen=True)
class FolderState:
    """Слепок папки из прошлого скана — по нему решаем, изменилась ли она."""

    mtime: float | None
    file_count: int


@dataclass
class RootScanResult:
    path: str
    kind: str
    is_available: bool
    is_writable: bool
    folders: list[ScannedFolder] = field(default_factory=list)
    error: str | None = None

    @property
    def total_bytes(self) -> int:
        def walk(folder: ScannedFolder) -> int:
            return folder.size_bytes + sum(walk(c) for c in folder.children)

        return sum(walk(f) for f in self.folders)


def check_writable(path: Path) -> bool:
    """Проверяет, доступен ли путь на запись.

    os.access недостаточно: при монтировании только для чтения он часто говорит,
    что запись разрешена, а реальная попытка падает на EROFS. Поэтому пробуем
    создать и удалить временный файл.
    """
    probe = path / ".a365-write-test"
    # Предупреждаем наблюдателя: сейчас в корне появится и исчезнет файл, и это
    # не изменение библиотеки. Без этого скан запускал сам себя по кругу —
    # см. SelfWrites в library_watcher.
    from app.services.library_watcher import self_writes

    self_writes.remember(probe)
    try:
        probe.touch()
    except OSError:
        return False
    try:
        probe.unlink()
    except OSError:
        # Создать смогли, удалить нет — считаем доступным, но это странно.
        log.warning("Не удалось удалить пробный файл %s", probe)
    return True


#: Сколько символов субтитров читаем, чтобы определить язык.
#:
#: У `.ass` первые пару тысяч символов — служебная шапка на латинице
#: (`[Script Info]`, `Style:`), поэтому берём заметно больше: нужен сам текст
#: реплик. Файл субтитров редко больше пары сотен килобайт, чтение дешёвое.
LANG_PROBE_CHARS = 20_000

#: Доля букв, начиная с которой письменность считается основной.
#:
#: Кириллица — 5%: у русского `.ass` половина файла всё равно латиница (шапка,
#: имена стилей, теги), но кириллицы там заведомо больше пяти процентов.
#: Кана — 2%: японский текст без каны не встречается, а два процента отделяют
#: настоящие реплики от случайного знака в названии шрифта.
CYRILLIC_SHARE = 0.05
KANA_SHARE = 0.02

#: Сколько букв нужно, чтобы вывод вообще имел смысл.
MIN_LETTERS = 50

#: Сколько латинских букв нужно, чтобы назвать файл английским.
#:
#: Порог намеренно выше общего. Кириллица и кана — **положительная** улика: они
#: не встречаются в служебной шапке, и раз они есть, вопрос закрыт. «Только
#: латиница» — улика от обратного, то есть слабая: шапка `.ass` сама по себе
#: латинская, и файл вообще без реплик объявлялся бы английским. Настоящие
#: субтитры набирают эти сотни букв первыми же строками диалога.
MIN_LATIN_FOR_ENGLISH = 400


def detect_subtitle_language(path: Path) -> str | None:
    """Язык субтитров по письменности, когда суффикса в имени нет.

    Не эвристика «на глаз»: русский, японский и английский пишутся **разными
    алфавитами**, и спутать их нельзя. Кана однозначно указывает на японский,
    кириллица — на русский, чистая латиница — на английский.

    Возвращает None, когда уверенности нет: пусть лучше язык останется
    неизвестным, чем субтитры уедут не в ту роль.
    """
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:LANG_PROBE_CHARS]
    except OSError:
        return None

    cyrillic = kana = latin = 0
    for ch in head:
        code = ord(ch)
        if 0x0400 <= code <= 0x04FF:
            cyrillic += 1
        elif 0x3040 <= code <= 0x30FF:
            kana += 1
        elif ch.isascii() and ch.isalpha():
            latin += 1

    letters = cyrillic + kana + latin
    if letters < MIN_LETTERS:
        return None
    if kana / letters >= KANA_SHARE:
        return "ja"
    if cyrillic / letters >= CYRILLIC_SHARE:
        return "ru"
    if latin == letters and latin >= MIN_LATIN_FOR_ENGLISH:
        return "en"
    return None


def _classify(suffix: str, rules: RuleSet) -> str | None:
    suffix = suffix.lower()
    if suffix in rules.video_ext:
        return "video"
    if suffix in rules.subtitle_ext:
        return "subtitle"
    if suffix in rules.metadata_ext:
        return "metadata"
    return None


def _season_from_dir_name(name: str) -> int | None:
    """Номер сезона по имени подпапки.

    ⚠️ Разбор общий с названиями (`extract_season`), а не свой. Раньше здесь
    лежала копия правил, и она понимала только «Season 2»: релизная подпапка
    `[Ohys-Raws] Kagerou Days 2nd Season (BD …)` оставалась без номера и уезжала
    на первый сезон. Формы «2nd Season», «S2» и «2 сезон» общий разбор знает
    давно — дублировать его смысла не было.
    """
    lowered = name.strip().lower()
    if lowered in SPECIALS_NAMES:
        return 0
    return extract_season(name)[1]


def scan_folder(
    folder: Path,
    root: Path,
    scope: str,
    rules: RuleSet,
    season_number: int | None = None,
    depth: int = 0,
    known: dict[str, FolderState] | None = None,
) -> ScannedFolder:
    """Рекурсивно обходит папку тайтла.

    `known` включает инкрементальный режим: папка, у которой совпали mtime и
    число файлов, не разбирается повторно. Экономия именно на разборе — XML
    каждого `.nfo` и цепочка регэкспов на каждое имя; обход каталогов остаётся,
    иначе изменение в подпапке осталось бы незамеченным (mtime родителя от него
    не меняется).

    ⚠️ Замена файла на месте под тем же именем mtime папки не меняет и здесь
    не ловится. Для этого есть сверочный полный скан по расписанию — он ходит
    без `known`.
    """
    rel = folder.relative_to(root).as_posix()
    nfo_data: NfoData | None = None
    nfo_rank = 0
    files: list[ScannedFile] = []
    children: list[ScannedFolder] = []
    size = 0
    # Эпизодные .nfo лежат рядом с видео и дают номер серии надёжнее имени файла.
    episode_nfo: dict[str, NfoData] = {}

    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        log.warning("Не удалось прочитать %s: %s", folder, exc)
        return ScannedFolder(
            rel_path=rel,
            name=folder.name,
            season_number=season_number,
            nfo=None,
            read_failed=True,
        )

    try:
        current_mtime: float | None = folder.stat().st_mtime
    except OSError:
        current_mtime = None

    file_entries = [
        e
        for e in entries
        if e.name not in SKIP_DIR_NAMES and not e.name.startswith(".") and not e.is_dir()
    ]
    relevant = [e for e in file_entries if _classify(e.suffix.lower(), rules) is not None]

    previous = (known or {}).get(rel)
    skip_files = (
        previous is not None
        and current_mtime is not None
        and previous.mtime == current_mtime
        and previous.file_count == len(relevant)
    )

    for entry in entries:
        if entry.name in SKIP_DIR_NAMES or entry.name.startswith("."):
            continue

        if entry.is_dir():
            # Глубже двух уровней от корня осмысленных структур не встречается,
            # но релизные подпапки внутри тайтла бывают — их тоже обходим.
            if depth < 2:
                children.append(
                    scan_folder(
                        entry,
                        root,
                        scope,
                        rules,
                        _season_from_dir_name(entry.name),
                        depth + 1,
                        known,
                    )
                )
            continue

        if skip_files:
            continue

        try:
            stat = entry.stat()
        except OSError:
            continue

        suffix = entry.suffix.lower()
        kind = _classify(suffix, rules)
        if kind is None:
            continue

        if suffix == ".nfo":
            parsed = parse_nfo(entry)
            if parsed is not None:
                if parsed.kind in ("tvshow", "season", "movie") and entry.stem in (
                    "tvshow",
                    "season",
                    "movie",
                ):
                    ranks = NFO_PRIORITY.get(scope, NFO_PRIORITY["series"])
                    rank = ranks.get(parsed.kind, 0)
                    if rank > nfo_rank:
                        nfo_data = parsed
                        nfo_rank = rank
                elif parsed.kind == "episode":
                    episode_nfo[entry.stem] = parsed

        size += stat.st_size
        parsed_name = parse_filename(entry.name, scope, rules)

        # Суффикса языка в имени нет — смотрим, какой письменностью написан
        # сам файл. Так подхватываются чужие сабы и наши старые, скачанные до
        # того, как языковой суффикс появился в правилах именования.
        language = parsed_name.language
        if language is None and kind == "subtitle":
            language = detect_subtitle_language(entry)
        files.append(
            ScannedFile(
                filename=entry.name,
                kind=kind,
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
                lang=language,
                season_number=parsed_name.season,
                episode=parsed_name.episode,
                episode_end=parsed_name.episode_end,
                matched_rule_id=parsed_name.matched_rule_id,
                release_group=parsed_name.release_group,
                anime365_translation_id=parsed_name.translation_id,
                excluded_reason=parsed_name.excluded_by,
            )
        )

    _apply_episode_nfo(files, episode_nfo)
    _apply_single_movie(files, scope)
    _pair_sidecar_subtitles(files)

    return ScannedFolder(
        rel_path=rel,
        name=folder.name,
        season_number=season_number,
        nfo=nfo_data,
        size_bytes=size,
        mtime=current_mtime,
        files=files,
        children=children,
        unchanged=skip_files,
    )


def _apply_episode_nfo(files: list[ScannedFile], episode_nfo: dict[str, NfoData]) -> None:
    """Уточняет номер серии данными из эпизодного .nfo.

    Эпизодный .nfo лежит рядом с видео и называется так же, поэтому связывается
    по основе имени. Он надёжнее разбора имени: там номер проставлен явно,
    а спешлы помечены нулевым сезоном.
    """
    for file in files:
        if file.kind != "video":
            continue
        stem = Path(file.filename).stem
        data = episode_nfo.get(stem)
        if data is None:
            continue
        if data.episode is not None:
            file.episode = float(data.episode)
            file.matched_rule_id = "episode_nfo"
        if data.season is not None:
            file.season_number = data.season
        if data.streams.subtitle_languages or data.streams.audio_languages:
            file.streams = data.streams.to_dict()


def _content_videos(files: list[ScannedFile]) -> list[ScannedFile]:
    return [f for f in files if f.kind == "video" and not f.excluded_reason]


def _apply_single_movie(files: list[ScannedFile], scope: str) -> None:
    """Единственное видео в папке фильма — это первая (и единственная) серия.

    У фильмов номера в имени нет и взяться ему неоткуда:
    «Chainsaw Man - The Movie - Reze Arc (2025) - [WEBDL-1080p]…» разбирается на
    название и год, а `episode` остаётся пустым. Связь же с тайтлом ставится
    только по номеру серии, поэтому фильм не показывался вовсе — при том, что
    файл лежит на диске и запись о нём в базе есть.

    Правило намеренно узкое: **ровно одно** видео. Составные фильмы (части,
    «Movies and OVAs») так не разложить, им нужен свой разбор — приписать всем
    единицу означало бы склеить разные части в одну серию.
    """
    if scope != "movie":
        return
    videos = _content_videos(files)
    if len(videos) != 1:
        return
    only = videos[0]
    if only.episode is None:
        only.episode = 1.0
        only.matched_rule_id = "single_movie"


def _pair_sidecar_subtitles(files: list[ScannedFile]) -> None:
    """Субтитры без номера серии берут его у своего видео.

    Сайдкар-субтитры называются по видеофайлу с добавленным хвостом
    (`…[JA].mkv` → `…[JA].ja.srt`) — именно по этому совпадению их подхватывает
    и плеер. Разбор имени тут бессилен: у фильмов номера нет ни в одном из имён,
    а у сериалов хвост языка иногда съедает номер вместе с собой.

    Берётся самое длинное имя видео, являющееся началом имени субтитров, —
    иначе в папке с «Part 1» и «Part 11» короткое имя перехватило бы чужие.
    """
    videos = [f for f in _content_videos(files) if f.episode is not None]
    if not videos:
        return
    by_stem = sorted(
        ((Path(f.filename).stem, f) for f in videos), key=lambda pair: len(pair[0]), reverse=True
    )

    for file in files:
        if file.kind != "subtitle" or file.episode is not None or file.excluded_reason:
            continue
        stem = Path(file.filename).stem
        for video_stem, video in by_stem:
            if stem == video_stem or stem.startswith(video_stem + "."):
                file.episode = video.episode
                file.episode_end = video.episode_end
                if file.season_number is None:
                    file.season_number = video.season_number
                file.matched_rule_id = "sidecar_of_video"
                break


def iter_title_folders(root: Path) -> Iterator[Path]:
    """Папки тайтлов внутри корня.

    Библиотека лежит как <корень>/library/<Тайтл>, но у пользователя корнем
    монтируется уже library, поэтому поддерживаем оба варианта.
    """
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError as exc:
        log.error("Корень %s недоступен: %s", root, exc)
        return

    # Если внутри лежит единственная папка library — спускаемся в неё.
    if len(entries) == 1 and entries[0].name.lower() == "library":
        yield from iter_title_folders(entries[0])
        return

    for entry in entries:
        if entry.name in SKIP_DIR_NAMES or entry.name.startswith("."):
            continue
        yield entry


def scan_root(
    path_str: str,
    kind: str,
    rules: RuleSet | None = None,
    known: dict[str, FolderState] | None = None,
) -> RootScanResult:
    """Сканирует один корень библиотеки.

    `known` — слепки папок из прошлого прохода. С ними скан инкрементальный:
    неизменившиеся папки не разбираются заново. Без них — полный сверочный.
    """
    rules = rules or get_rules()
    root = Path(path_str)

    if not root.is_dir():
        return RootScanResult(
            path=path_str,
            kind=kind,
            is_available=False,
            is_writable=False,
            error="Путь недоступен или не является папкой",
        )

    writable = check_writable(root)
    if not writable:
        log.info("Корень %s смонтирован только для чтения — загрузки будут недоступны", path_str)

    scope = "movie" if kind == "movie" else "series"
    folders = [
        scan_folder(folder, folder.parent, scope, rules, known=known)
        for folder in iter_title_folders(root)
    ]

    return RootScanResult(
        path=path_str,
        kind=kind,
        is_available=True,
        is_writable=writable,
        folders=folders,
    )


def summarize(result: RootScanResult) -> dict[str, Any]:
    """Сводка для отчёта и индикаторов в UI."""
    videos = subs = unmatched = excluded = 0
    langs: dict[str, int] = {}

    def walk(folder: ScannedFolder) -> None:
        nonlocal videos, subs, unmatched, excluded
        for file in folder.files:
            if file.excluded_reason:
                excluded += 1
                continue
            if file.kind == "video":
                videos += 1
                if file.is_unmatched:
                    unmatched += 1
            elif file.kind == "subtitle":
                subs += 1
                langs[file.lang or "unknown"] = langs.get(file.lang or "unknown", 0) + 1
        for child in folder.children:
            walk(child)

    for folder in result.folders:
        walk(folder)

    return {
        "path": result.path,
        "kind": result.kind,
        "available": result.is_available,
        "writable": result.is_writable,
        "title_folders": len(result.folders),
        "videos": videos,
        "unmatched_videos": unmatched,
        "excluded_files": excluded,
        "subtitles": subs,
        "subtitle_languages": langs,
        "total_bytes": result.total_bytes,
        "scanned_at": datetime.utcnow().isoformat(),
    }
