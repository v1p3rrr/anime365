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

from app.services.filename_rules import RuleSet, get_rules, parse_filename
from app.services.nfo_parser import NfoData, parse_nfo

log = logging.getLogger(__name__)

# Папки, которые не являются тайтлами: служебное и временное.
SKIP_DIR_NAMES = frozenset({"tmp", ".tmp", "@eaDir", ".@__thumb", "lost+found", ".git"})

# Имена подпапок, означающие спешлы. Номер сезона у них — 0.
SPECIALS_NAMES = frozenset({"specials", "special", "extras", "ova", "ovas"})


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

    @property
    def video_count(self) -> int:
        return sum(1 for f in self.files if f.kind == "video" and not f.excluded_reason)


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
    lowered = name.strip().lower()
    if lowered in SPECIALS_NAMES:
        return 0
    # «Season 2», «сезон 2»
    import re

    match = re.search(r"(?i)\b(?:season|сезон)\s*(\d{1,2})\b", name)
    if match:
        return int(match.group(1))
    return None


def scan_folder(
    folder: Path,
    root: Path,
    scope: str,
    rules: RuleSet,
    season_number: int | None = None,
    depth: int = 0,
) -> ScannedFolder:
    """Рекурсивно обходит папку тайтла."""
    rel = folder.relative_to(root).as_posix()
    nfo_data: NfoData | None = None
    files: list[ScannedFile] = []
    children: list[ScannedFolder] = []
    size = 0
    # Эпизодные .nfo лежат рядом с видео и дают номер серии надёжнее имени файла.
    episode_nfo: dict[str, NfoData] = {}

    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        log.warning("Не удалось прочитать %s: %s", folder, exc)
        return ScannedFolder(rel_path=rel, name=folder.name, season_number=season_number, nfo=None)

    for entry in entries:
        if entry.name in SKIP_DIR_NAMES or entry.name.startswith("."):
            continue

        if entry.is_dir():
            # Глубже двух уровней от корня осмысленных структур не встречается,
            # но релизные подпапки внутри тайтла бывают — их тоже обходим.
            if depth < 2:
                children.append(
                    scan_folder(
                        entry, root, scope, rules, _season_from_dir_name(entry.name), depth + 1
                    )
                )
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
                    nfo_data = parsed
                elif parsed.kind == "episode":
                    episode_nfo[entry.stem] = parsed

        size += stat.st_size
        parsed_name = parse_filename(entry.name, scope, rules)
        files.append(
            ScannedFile(
                filename=entry.name,
                kind=kind,
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
                lang=parsed_name.language,
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

    try:
        mtime = folder.stat().st_mtime
    except OSError:
        mtime = None

    return ScannedFolder(
        rel_path=rel,
        name=folder.name,
        season_number=season_number,
        nfo=nfo_data,
        size_bytes=size,
        mtime=mtime,
        files=files,
        children=children,
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


def scan_root(path_str: str, kind: str, rules: RuleSet | None = None) -> RootScanResult:
    """Сканирует один корень библиотеки."""
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
        scan_folder(folder, folder.parent, scope, rules)
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
