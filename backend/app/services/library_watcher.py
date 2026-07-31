"""Слежение за библиотекой через inotify (watchdog).

Зачем: библиотека в UI остаётся актуальной сразу после появления файлов, а не
через часы ожидания расписания. По ресурсам это почти бесплатно — следим за
каталогами, а не файлами, и одна inotify-watch занимает около килобайта памяти
ядра, то есть пара мегабайт на всю библиотеку.

⚠️ У inotify два тихих режима отказа, и оба надо ловить явно:

  1. Сетевые ФС (NFS, SMB): события от других хостов не доставляются вовсе.
     Слежение «работает», но не видит ничего.
  2. Упор в fs.inotify.max_user_watches: на старых системах лимит 8192, и он
     общий на пользователя — Jellyfin и Sonarr тратят его из того же лимита.

В обоих случаях откатываемся на расписание, пишем предупреждение и показываем
состояние в UI: пользователь должен знать, что живёт на расписании.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.core.config import settings
from app.services.filename_rules import get_rules

log = logging.getLogger(__name__)

INOTIFY_LIMIT_FILE = Path("/proc/sys/fs/inotify/max_user_watches")

# Файловые системы, на которых inotify не работает как ожидается.
NETWORK_FS = ("nfs", "nfs4", "cifs", "smb", "smbfs", "fuse.sshfs", "afs")

# Запас к числу каталогов: Jellyfin и Sonarr расходуют watch'и из того же лимита.
WATCH_HEADROOM = 3


class WatcherState:
    ACTIVE = "active"
    FALLBACK = "fallback"
    DISABLED = "disabled"


@dataclass
class WatcherStatus:
    state: str = WatcherState.DISABLED
    #: Машинный код причины — перевод живёт на клиенте. Раньше здесь была
    #: готовая русская фраза, и в английском интерфейсе она торчала как есть.
    reason: str | None = None
    #: Подстановки к коду причины: числа и пути, которые нужны в тексте.
    reason_params: dict = field(default_factory=dict)
    watched_roots: list[str] = field(default_factory=list)
    directories: int = 0
    events_seen: int = 0
    last_event_at: datetime | None = None
    last_rescan_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "reason": self.reason,
            "reason_params": self.reason_params,
            "watched_roots": self.watched_roots,
            "directories": self.directories,
            "events_seen": self.events_seen,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
            "last_rescan_at": self.last_rescan_at.isoformat() if self.last_rescan_at else None,
        }


def detect_filesystem(path: Path) -> str | None:
    """Тип файловой системы для пути. Только Linux; на других системах None."""
    mounts = Path("/proc/mounts")
    if not mounts.is_file():
        return None

    try:
        entries = []
        for line in mounts.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 3:
                entries.append((parts[1], parts[2]))
    except OSError:
        return None

    resolved = str(path.resolve())
    # Выбираем самую длинную подходящую точку монтирования.
    best: tuple[str, str] | None = None
    for mount_point, fs_type in entries:
        if resolved == mount_point or resolved.startswith(mount_point.rstrip("/") + "/"):
            if best is None or len(mount_point) > len(best[0]):
                best = (mount_point, fs_type)
    return best[1] if best else None


def count_directories(root: Path, limit: int = 100_000) -> int:
    """Сколько каталогов придётся отслеживать. С потолком, чтобы не зависнуть."""
    total = 0
    try:
        for _, dirs, _ in _walk(root):
            total += len(dirs)
            if total > limit:
                return limit
    except OSError:
        pass
    return total


def _walk(root: Path):
    import os

    yield from os.walk(root, onerror=lambda _: None)


def read_inotify_limit() -> int | None:
    try:
        return int(INOTIFY_LIMIT_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def check_can_watch(roots: list[Path]) -> tuple[bool, str | None, int, dict[str, str | int]]:
    """Проверяет, имеет ли смысл запускать слежение.

    Возвращает (можно, КОД причины отказа, число каталогов, подстановки).

    Код, а не готовая фраза: перевод живёт на клиенте, а числа и пути подаются
    отдельно как подстановки. Раньше сюда собиралась русская строка, и в
    английском интерфейсе она показывалась как есть.
    """
    for root in roots:
        fs_type = detect_filesystem(root)
        if fs_type and fs_type.lower() in NETWORK_FS:
            return (False, "network_fs", 0, {"root": str(root), "fs": fs_type})

    directories = sum(count_directories(root) for root in roots)

    limit = read_inotify_limit()
    if limit is not None and directories * WATCH_HEADROOM > limit:
        return (
            False,
            "inotify_limit",
            directories,
            {"directories": directories, "limit": limit},
        )

    return True, None, directories, {}


class _DebouncingHandler(FileSystemEventHandler):
    """Копит события и отдаёт затронутые папки после паузы.

    Копирование одного файла порождает десятки событий, а Jellyfin и Sonarr
    дополнительно пишут .nfo и постеры рядом с видео. Без дебаунса сканирование
    запускалось бы на каждый чанк записи.
    """

    def __init__(self, on_change: Callable[[set[Path]], None], debounce: float) -> None:
        self._on_change = on_change
        self._debounce = debounce
        self._pending: dict[Path, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._flush_loop, daemon=True, name="a365-watch")
        self.events_seen = 0
        self.last_event_at: datetime | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ #

    def _interesting(self, path_str: str) -> bool:
        rules = get_rules()
        suffix = Path(path_str).suffix.lower()
        return suffix in (rules.video_ext | rules.subtitle_ext) or suffix == ".nfo"

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            # Создание и удаление папок тоже важно: это новый или пропавший тайтл.
            target = Path(event.src_path)
        else:
            if not self._interesting(event.src_path):
                return
            target = Path(event.src_path).parent

        self.events_seen += 1
        self.last_event_at = datetime.utcnow()
        with self._lock:
            self._pending[target] = time.monotonic()

    def _flush_loop(self) -> None:
        while not self._stop.wait(1.0):
            now = time.monotonic()
            ready: set[Path] = set()
            with self._lock:
                for path, stamp in list(self._pending.items()):
                    if now - stamp >= self._debounce:
                        ready.add(path)
                        del self._pending[path]
            if ready:
                try:
                    self._on_change(ready)
                except Exception:
                    # Падение обработчика не должно останавливать слежение.
                    log.exception("Обработка изменений файловой системы упала")


class LibraryWatcher:
    def __init__(self) -> None:
        self.status = WatcherStatus()
        self._observer: Observer | None = None
        self._handler: _DebouncingHandler | None = None
        self._on_change: Callable[[set[Path]], None] | None = None

    def set_callback(self, callback: Callable[[set[Path]], None]) -> None:
        self._on_change = callback

    def start(self) -> WatcherStatus:
        if not settings.get_bool("watch_filesystem"):
            self.status = WatcherStatus(
                state=WatcherState.DISABLED, reason="disabled_in_settings"
            )
            return self.status

        roots = [
            Path(settings.get_str("series_root")),
            Path(settings.get_str("movies_root")),
        ]
        existing = [r for r in roots if r.is_dir()]
        if not existing:
            self.status = WatcherStatus(
                state=WatcherState.FALLBACK, reason="no_available_root"
            )
            log.warning("Слежение не запущено: %s", self.status.reason)
            return self.status

        can_watch, reason, directories, params = check_can_watch(existing)
        if not can_watch:
            self.status = WatcherStatus(
                state=WatcherState.FALLBACK,
                reason=reason,
                reason_params=params,
                directories=directories
            )
            log.warning("Слежение недоступно, работаем по расписанию: %s", reason)
            return self.status

        handler = _DebouncingHandler(
            on_change=self._handle_change,
            debounce=float(settings.get_int("watch_debounce_seconds")),
        )
        observer = Observer()
        try:
            for root in existing:
                observer.schedule(handler, str(root), recursive=True)
            observer.start()
        except OSError as exc:
            # Упор в лимит проявляется именно здесь, даже если расчёт прошёл.
            self.status = WatcherStatus(
                state=WatcherState.FALLBACK,
                reason="subscribe_failed",
                directories=directories,
            )
            log.warning("Слежение не запустилось: %s", exc)
            return self.status

        handler.start()
        self._observer = observer
        self._handler = handler
        self.status = WatcherStatus(
            state=WatcherState.ACTIVE,
            watched_roots=[str(r) for r in existing],
            directories=directories,
        )
        log.info("Слежение за файловой системой запущено: каталогов %d", directories)
        return self.status

    def stop(self) -> None:
        if self._handler is not None:
            self._handler.stop()
            self._handler = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self.status = WatcherStatus(state=WatcherState.DISABLED, reason="stopped")

    def refresh_status(self) -> WatcherStatus:
        if self._handler is not None:
            self.status.events_seen = self._handler.events_seen
            self.status.last_event_at = self._handler.last_event_at
        return self.status

    def _handle_change(self, folders: set[Path]) -> None:
        log.info("Изменения в библиотеке: затронуто папок %d", len(folders))
        self.status.last_rescan_at = datetime.utcnow()
        if self._on_change is not None:
            self._on_change(folders)


library_watcher = LibraryWatcher()
