"""Порядок списка загрузок: у двух его половин он разный.

**Активные** идут в порядке скачивания — воркер берёт наименьшую позицию,
значит сверху должна стоять та задача, что пойдёт первой. Иначе перетаскивание
показывало бы одно, а качалось бы другое.

**Завершённые** — от свежего к давнему: их разбирают сверху вниз, и вчерашняя
загрузка интереснее позапрошлогодней.

⚠️ Тест появился по замечанию пользователя. Сортировка стояла одна на обе
половины — по позиции вверх, — поэтому завершённые показывались от самых
старых. В комментарии рядом при этом было написано, как задумано, а не как
получилось: сам комментарий и стал единственным «доказательством» правильности.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.downloads import DownloadTask, TaskStatus

SERIES = 890_001
NOW = datetime(2026, 8, 17, 12, 0, 0)


def _task(
    status: TaskStatus,
    translation_id: int,
    *,
    finished: datetime | None = None,
    created: datetime | None = None,
) -> int:
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
            finished_at=finished,
            created_at=created or NOW,
        )
        session.add(task)
        session.flush()
        return task.id


def _ids(client, ours: set[int]) -> list[int]:
    """Идентификаторы из ответа, только наши: база у тестов общая."""
    tasks = client.get("/api/downloads").json()["tasks"]
    return [task["id"] for task in tasks if task["id"] in ours]


class TestFinishedGoNewestFirst:
    def test_the_freshest_download_is_on_top(self, client) -> None:
        old = _task(TaskStatus.DONE, 920_001, finished=NOW - timedelta(days=30))
        recent = _task(TaskStatus.DONE, 920_002, finished=NOW - timedelta(minutes=5))
        middle = _task(TaskStatus.DONE, 920_003, finished=NOW - timedelta(days=2))

        assert _ids(client, {old, recent, middle}) == [recent, middle, old]

    def test_a_task_without_a_finish_time_falls_back_to_creation(self, client) -> None:
        """Такие остались от времён, когда `finished_at` ещё не проставляли."""
        older = _task(TaskStatus.CANCELLED, 920_004, created=NOW - timedelta(days=10))
        newer = _task(TaskStatus.DONE, 920_005, finished=NOW - timedelta(days=1))

        assert _ids(client, {older, newer}) == [newer, older]


class TestActiveKeepQueueOrder:
    def test_active_tasks_follow_their_position(self, client) -> None:
        first = _task(TaskStatus.QUEUED, 920_006)
        second = _task(TaskStatus.QUEUED, 920_007)

        assert _ids(client, {first, second}) == [first, second]

    def test_active_come_before_finished(self, client) -> None:
        done = _task(TaskStatus.DONE, 920_008, finished=NOW)
        queued = _task(TaskStatus.QUEUED, 920_009)

        assert _ids(client, {done, queued}) == [queued, done]
