"""Сканирование не должно запускать само себя.

Живая поломка: скан вызывает `check_writable`, тот создаёт и удаляет в корне
`.a365-write-test`, inotify сообщает об изменении, наблюдатель через дебаунс
запускает следующий скан — и так по кругу. В логах ровно каждые ~72 секунды
«затронуто папок 1», процессор на 101%, сканирование не заканчивается никогда.
"""

from __future__ import annotations

from pathlib import Path

from app.services.library_scan import check_writable
from app.services.library_watcher import _DebouncingHandler, self_writes


class _Event:
    """Событие watchdog в объёме, который читает обработчик."""

    def __init__(self, path: Path, *, is_directory: bool, event_type: str = "created") -> None:
        self.src_path = str(path)
        self.is_directory = is_directory
        self.event_type = event_type


def _handler() -> _DebouncingHandler:
    self_writes.clear()
    # Обработчик берём без потока: нас интересует только отбор событий.
    return _DebouncingHandler(on_change=lambda _paths: None, debounce=10.0)


def _pending(handler: _DebouncingHandler) -> set[str]:
    return {str(p) for p in handler._pending}


class TestSelfWritesAreIgnored:
    def test_write_probe_does_not_schedule_a_scan(self, tmp_path: Path) -> None:
        handler = _handler()
        check_writable(tmp_path)

        probe = tmp_path / ".a365-write-test"
        handler.on_any_event(_Event(probe, is_directory=False))
        handler.on_any_event(_Event(probe, is_directory=False, event_type="deleted"))
        # Тот самый второй сигнал: inotify сообщает и об изменении самой папки.
        handler.on_any_event(_Event(tmp_path, is_directory=True, event_type="created"))

        assert _pending(handler) == set(), "скан запустил бы сам себя"

    def test_directory_modified_is_never_a_reason(self, tmp_path: Path) -> None:
        """mtime папки меняется от любого действия внутри — это не сигнал."""
        handler = _handler()

        handler.on_any_event(_Event(tmp_path, is_directory=True, event_type="modified"))

        assert _pending(handler) == set()


class TestRealChangesStillArrive:
    """Обратная страховка: подавление не должно проглотить настоящие события."""

    def test_new_video_schedules_a_scan(self, tmp_path: Path) -> None:
        handler = _handler()
        video = tmp_path / "Show S01E01 [Group] WEBDL-1080p.mkv"

        handler.on_any_event(_Event(video, is_directory=False))

        assert _pending(handler) == {str(tmp_path)}

    def test_new_folder_schedules_a_scan(self, tmp_path: Path) -> None:
        handler = _handler()
        folder = tmp_path / "Новый тайтл"

        handler.on_any_event(_Event(folder, is_directory=True, event_type="created"))

        assert _pending(handler) == {str(folder)}

    def test_suppression_expires(self, tmp_path: Path, monkeypatch) -> None:
        """Через несколько секунд тот же путь снова считается чужим."""
        import app.services.library_watcher as module

        handler = _handler()
        probe = tmp_path / ".a365-write-test"
        self_writes.remember(probe)

        assert self_writes.matches(str(probe)) is True

        monkeypatch.setattr(
            module.time, "monotonic", lambda: module.time.perf_counter() + 10_000
        )
        assert self_writes.matches(str(probe)) is False
