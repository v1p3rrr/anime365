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
    """Задача в очереди — так же, как её заводит `enqueue`.

    Позицию выставляем тем же способом, что и рабочий код: без неё порядком
    служит `id`, и тест сравнивал бы None с None вместо настоящих позиций.
    """
    from app.services.download_queue import _next_position

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
            position=_next_position(session),
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
            # Позиция — явное число, а не производная от времени: порядок
            # задаёт пользователь перетаскиванием, а время постановки
            # показывается в истории и подменять его нельзя.
            assert task.position is not None
            others = session.query(DownloadTask).filter(DownloadTask.id != task_id).all()
            assert all(
                (other.position or other.id) <= task.position for other in others
            ), "задача не оказалась в конце очереди"

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


class TestOrder:
    """Очередь идёт строго сверху вниз по тому порядку, что видит пользователь."""

    def _queued(self, *translation_ids: int) -> list[int]:
        return [_task(TaskStatus.QUEUED, tid) for tid in translation_ids]

    def _next_claimed(self) -> int | None:
        from app.services.download_queue import download_queue

        return download_queue._claim_next()

    def test_new_task_goes_to_the_end(self, client) -> None:
        """Новая задача не должна лезть вперёд уже поставленных."""
        first, second = self._queued(920001, 920002)
        with session_scope() as session:
            positions = {
                task_id: session.get(DownloadTask, task_id).position
                for task_id in (first, second)
            }
        assert positions[first] < positions[second]

    def test_reorder_changes_who_goes_first(self, client) -> None:
        first, second, third = self._queued(920011, 920012, 920013)

        response = client.post(
            "/api/downloads/reorder", json={"ids": [third, first, second]}
        )
        assert response.status_code == 200

        with session_scope() as session:
            order = sorted(
                (first, second, third),
                key=lambda i: session.get(DownloadTask, i).position,
            )
        assert order == [third, first, second]

    def test_reorder_refuses_unknown_ids(self, client) -> None:
        """Список у клиента мог устареть — молча пропускать это нельзя."""
        task_id = _task(TaskStatus.QUEUED, 920021)
        response = client.post(
            "/api/downloads/reorder", json={"ids": [task_id, 99999999]}
        )
        assert response.status_code == 404

    def test_failed_task_moves_to_the_end(self, client) -> None:
        """Упавшая уходит вниз: её ошибка часто повторяется при каждой попытке.

        Иначе она осталась бы первой и упиралась бы в ту же ошибку раньше
        всего остального, не давая очереди двигаться.
        """
        from app.services.download_queue import _fail_with_backoff

        first, second = self._queued(920031, 920032)
        _fail_with_backoff(first, "сеть отвалилась")

        with session_scope() as session:
            failed = session.get(DownloadTask, first)
            other = session.get(DownloadTask, second)
            # Попытки ещё есть, поэтому задача снова в очереди — но в конце.
            assert failed.status == TaskStatus.QUEUED
            assert failed.position > other.position


class TestPrioritize:
    """Подъём в начало очереди — зеркало отправки в конец."""

    def test_moves_to_the_front(self, client) -> None:
        first = _task(TaskStatus.QUEUED, 930001)
        last = _task(TaskStatus.QUEUED, 930002)

        assert client.post(f"/api/downloads/{last}/prioritize").status_code == 200

        with session_scope() as session:
            assert session.get(DownloadTask, last).position < session.get(
                DownloadTask, first
            ).position

    def test_clears_backoff(self, client) -> None:
        """«Сделай сейчас» не должно упираться в паузу перед повтором."""
        from datetime import datetime, timedelta

        task_id = _task(TaskStatus.QUEUED, 930003)
        with session_scope() as session:
            session.get(DownloadTask, task_id).retry_after = datetime.utcnow() + timedelta(
                hours=1
            )

        client.post(f"/api/downloads/{task_id}/prioritize")

        with session_scope() as session:
            assert session.get(DownloadTask, task_id).retry_after is None

    def test_finished_task_is_refused(self, client) -> None:
        task_id = _task(TaskStatus.DONE, 930004)
        assert client.post(f"/api/downloads/{task_id}/prioritize").status_code == 400
