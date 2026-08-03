"""Ручное управление очередью: перезапуск, сдвиг в конец, удаление.

Три пробела, которые нашёл пользователь: зависшую загрузку нельзя было
перезапустить, нельзя было пропустить её вперёд другими, и нельзя было убрать
из списка ошибочную задачу. Последнее особенно мешало: перевод со вшитыми
субтитрами падает при каждой попытке и висит в списке вечно.
"""

from __future__ import annotations

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.downloads import DownloadTask, TaskStatus

SERIES = 880001


def _task(status: TaskStatus, translation_id: int) -> int:
    with session_scope() as session:
        if session.get(Series, SERIES) is None:
            session.add(Series(id=SERIES, title="Тайтл", type="tv"))
            session.flush()
        task = DownloadTask(
            translation_id=translation_id,
            series_id=SERIES,
            kind="video",
            status=status,
            series_title="Тайтл",
            created_by_user_id=1,
            active_key=translation_id,
        )
        session.add(task)
        session.flush()
        return task.id


class TestRequeue:
    def test_moves_to_the_end(self, client) -> None:
        task_id = _task(TaskStatus.QUEUED, 910001)
        assert client.post(f"/api/downloads/{task_id}/requeue").status_code == 200

        with session_scope() as session:
            task = session.get(DownloadTask, task_id)
            assert task.status == TaskStatus.QUEUED
            # Именно отдельное поле: время постановки трогать нельзя, оно
            # показывается в истории.
            assert task.queued_at is not None
            assert task.queued_at >= task.created_at

    def test_finished_task_is_refused(self, client) -> None:
        """У завершённой задачи очереди нет — двигать нечего."""
        task_id = _task(TaskStatus.DONE, 910002)
        assert client.post(f"/api/downloads/{task_id}/requeue").status_code == 400

    def test_unknown_task(self, client) -> None:
        assert client.post("/api/downloads/99999999/requeue").status_code == 404


class TestDelete:
    def test_failed_task_can_be_removed(self, client) -> None:
        """Главный случай: ошибка, которая повторится при любой попытке."""
        task_id = _task(TaskStatus.FAILED, 910003)
        assert client.delete(f"/api/downloads/{task_id}").status_code == 200
        with session_scope() as session:
            assert session.get(DownloadTask, task_id) is None

    def test_queued_task_can_be_removed(self, client) -> None:
        task_id = _task(TaskStatus.QUEUED, 910004)
        assert client.delete(f"/api/downloads/{task_id}").status_code == 200
        with session_scope() as session:
            assert session.get(DownloadTask, task_id) is None

    def test_unknown_task(self, client) -> None:
        assert client.delete("/api/downloads/99999999").status_code == 404


class TestClearFailed:
    def test_removes_only_failed(self, client) -> None:
        failed = _task(TaskStatus.FAILED, 910005)
        done = _task(TaskStatus.DONE, 910006)

        response = client.post("/api/downloads/clear-failed")
        assert response.status_code == 200

        with session_scope() as session:
            assert session.get(DownloadTask, failed) is None
            # Успешные — это история, её чистит другая кнопка.
            assert session.get(DownloadTask, done) is not None
