"""Конвейер загрузок: разделение на скачивание и постобработку.

Проверяем переходы состояний, а не сеть. Опасность здесь в том, что задача
незаметно застревает: воркер её не забирает, а в интерфейсе она выглядит
работающей.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.db import Base, engine, session_scope
from app.models.downloads import DownloadTask, TaskStatus
from app.services.download_queue import DownloadQueue


@pytest.fixture(autouse=True)
def schema():
    Base.metadata.create_all(engine)
    yield
    with session_scope() as session:
        for task in session.query(DownloadTask).all():
            session.delete(task)


def _task(status: str, **kwargs) -> int:
    with session_scope() as session:
        task = DownloadTask(
            translation_id=kwargs.pop("translation_id", 1),
            series_id=1,
            kind="video",
            status=status,
            active_key=kwargs.pop("active_key", 0),
            **kwargs,
        )
        session.add(task)
        session.flush()
        return task.id


def _status(task_id: int) -> str:
    with session_scope() as session:
        return session.get(DownloadTask, task_id).status


class TestRecoveryAfterRestart:
    """Статусы `downloading` и `postprocessing` означают «занят воркер».

    После падения контейнера воркеров нет, а метки остаются — и такие задачи
    не забирал никто: `_claim_next` берёт только `queued`. Загрузка, прерванная
    перезапуском, зависала навсегда, хотя обещано продолжение с того же места.
    """

    def test_downloading_returns_to_queue(self) -> None:
        task_id = _task(TaskStatus.DOWNLOADING, translation_id=101, speed_bps=500.0)

        DownloadQueue._recover_interrupted()

        assert _status(task_id) == TaskStatus.QUEUED
        with session_scope() as session:
            # Скорость из прошлой жизни в интерфейсе выглядела бы как активность.
            assert session.get(DownloadTask, task_id).speed_bps == 0.0

    def test_postprocessing_returns_to_move_not_to_download(self) -> None:
        task_id = _task(TaskStatus.POSTPROCESSING, translation_id=102)

        DownloadQueue._recover_interrupted()

        # Файл уже скачан целиком — качать его заново незачем.
        assert _status(task_id) == TaskStatus.DOWNLOADED

    def test_finished_tasks_are_untouched(self) -> None:
        done = _task(TaskStatus.DONE, translation_id=103, active_key=103)
        failed = _task(TaskStatus.FAILED, translation_id=104, active_key=104)
        paused = _task(TaskStatus.PAUSED, translation_id=105)

        DownloadQueue._recover_interrupted()

        assert _status(done) == TaskStatus.DONE
        assert _status(failed) == TaskStatus.FAILED
        # Пауза — осознанное решение пользователя, восстановление её не отменяет.
        assert _status(paused) == TaskStatus.PAUSED


class TestPostprocessClaim:
    def test_claims_downloaded_task(self, tmp_path: Path) -> None:
        task_id = _task(
            TaskStatus.DOWNLOADED,
            translation_id=201,
            tmp_path=str(tmp_path / "a365-1.mp4"),
            target_path=str(tmp_path / "lib" / "Show S01E01.mp4"),
        )

        queue = DownloadQueue()
        claimed = queue._claim_next_postprocess()

        assert claimed is not None
        claimed_id, tmp, target = claimed
        assert claimed_id == task_id
        assert tmp.name == "a365-1.mp4"
        assert target.name == "Show S01E01.mp4"
        # Метка сразу переставлена, иначе второй воркер забрал бы ту же задачу.
        assert _status(task_id) == TaskStatus.POSTPROCESSING

    def test_ignores_tasks_that_are_still_downloading(self) -> None:
        _task(TaskStatus.DOWNLOADING, translation_id=202)

        assert DownloadQueue()._claim_next_postprocess() is None

    def test_task_without_paths_fails_instead_of_looping(self) -> None:
        # Пути пишутся до начала загрузки, так что это ненормальное состояние.
        # Молча оставить его в очереди — значит крутить воркер вхолостую.
        task_id = _task(TaskStatus.DOWNLOADED, translation_id=203)

        assert DownloadQueue()._claim_next_postprocess() is None
        assert _status(task_id) == TaskStatus.FAILED

    def test_empty_queue_returns_none(self) -> None:
        assert DownloadQueue()._claim_next_postprocess() is None
