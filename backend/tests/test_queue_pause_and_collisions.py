"""Пауза, отмена и столкновение имён при раскладке.

Три дефекта одного ревью, и все три про то, что механизм делал не то, что
обещала подпись рядом с ним.

* **Пауза активной загрузки работала как отмена.** И пауза, и отмена шли
  воркеру через одно множество `_cancelled`, различить их он не мог — и удалял
  недокачанный `.part`, помечая задачу отменённой. Комментарий рядом при этом
  обещал «переведёт в paused, сохранив .part».
* **Общая пауза помечала задачи `paused`**, а `resume_all` снимал только флаг в
  памяти. Задачи оставались в `paused`, воркер берёт лишь `queued` — и те
  одна-две загрузки, что шли в момент паузы, стояли навсегда.
* **Готовый файл перезаписывался молча.** `shutil.move` кладёт файл поверх
  существующего, а [06-naming-rules.md](../../claude/06-naming-rules.md)
  обещает обратное: не перетирать и добавлять ` v2`.
"""

from __future__ import annotations

import pytest

from app.services.download_queue import DownloadQueue, QueueError, free_destination


class TestStopReason:
    """Почему загрузка прервалась. Три исхода, а не два."""

    def test_cancel_is_a_cancel(self) -> None:
        queue = DownloadQueue()
        queue.cancel(7)

        assert queue._stop_reason(7) == "cancelled"

    def test_pause_is_a_pause(self) -> None:
        queue = DownloadQueue()
        queue.pause(7)

        assert queue._stop_reason(7) == "paused"

    def test_pause_and_cancel_do_not_share_a_set(self) -> None:
        """⚠️ Главное в этой правке: раньше пауза шла тем же путём, что отмена.

        Из-за этого воркер удалял скачанное и менял статус на «отменено» —
        то есть кнопка «пауза» теряла файл.
        """
        queue = DownloadQueue()
        queue.pause(7)

        assert 7 not in queue._cancelled

    def test_a_global_pause_sends_the_task_back_to_the_queue(self) -> None:
        """Задача ничем не провинилась — помечать её `paused` нельзя.

        Иначе общее возобновление её не поднимет: оно статусов не трогает, а
        воркер берёт только `queued`.
        """
        queue = DownloadQueue()
        queue.pause_all()

        assert queue._stop_reason(7) == "queued"

    def test_the_mark_is_cleared_after_reading(self) -> None:
        queue = DownloadQueue()
        queue.cancel(7)
        queue._stop_reason(7)

        assert queue._stop_reason(7) == "queued", "отметку надо снимать, иначе она залипнет"

    def test_cancel_wins_over_pause(self) -> None:
        """Отказ от файла сильнее просьбы подождать."""
        queue = DownloadQueue()
        queue.pause(7)
        queue.cancel(7)

        assert queue._stop_reason(7) == "cancelled"

    def test_resume_forgets_the_pause(self) -> None:
        queue = DownloadQueue()
        queue.pause(7)
        queue.forget_paused(7)

        assert queue._stop_reason(7) == "queued"


class TestPauseEndpoint:
    """Эндпоинт обязан просить именно паузу, а не отмену."""

    def test_pausing_a_running_task_asks_for_a_pause(self, client) -> None:
        from app.models.downloads import TaskStatus
        from app.services.download_queue import download_queue
        from tests.test_queue_actions import _task

        task_id = _task(TaskStatus.DOWNLOADING, 915001)
        try:
            client.post(f"/api/downloads/{task_id}/pause")

            assert task_id in download_queue._paused_tasks
            assert task_id not in download_queue._cancelled
        finally:
            download_queue.forget_paused(task_id)

    def test_resuming_takes_the_request_back(self, client) -> None:
        """Иначе воркер остановил бы задачу сразу после возвращения в очередь."""
        from app.models.downloads import TaskStatus
        from app.services.download_queue import download_queue
        from tests.test_queue_actions import _task

        task_id = _task(TaskStatus.PAUSED, 915002)
        client.post(f"/api/downloads/{task_id}/pause")
        client.post(f"/api/downloads/{task_id}/resume")

        assert task_id not in download_queue._paused_tasks


class TestFreeDestination:
    """Куда класть файл, если имя занято."""

    def _file(self, path, size: int):
        path.write_bytes(b"x" * size)
        return path

    def test_a_free_name_is_taken_as_is(self, tmp_path) -> None:
        source = self._file(tmp_path / "source.mp4", 10)
        destination = tmp_path / "Show S01E01.mp4"

        assert free_destination(destination, source) == destination

    def test_the_same_file_means_nothing_to_do(self, tmp_path) -> None:
        """Размеры совпали — второй экземпляр не нужен."""
        source = self._file(tmp_path / "source.mp4", 10)
        destination = self._file(tmp_path / "Show S01E01.mp4", 10)

        assert free_destination(destination, source) is None

    def test_a_different_file_is_not_overwritten(self, tmp_path) -> None:
        """⚠️ Под этим именем может лежать файл, который мы не скачивали."""
        source = self._file(tmp_path / "source.mp4", 10)
        destination = self._file(tmp_path / "Show S01E01.mp4", 999)

        result = free_destination(destination, source)

        assert result == tmp_path / "Show S01E01 v2.mp4"
        assert destination.read_bytes() == b"x" * 999, "старый файл обязан остаться целым"

    def test_the_variant_number_grows(self, tmp_path) -> None:
        source = self._file(tmp_path / "source.mp4", 10)
        destination = self._file(tmp_path / "Show S01E01.mp4", 999)
        self._file(tmp_path / "Show S01E01 v2.mp4", 998)

        assert free_destination(destination, source) == tmp_path / "Show S01E01 v3.mp4"

    def test_giving_up_is_an_error_and_not_an_overwrite(self, tmp_path) -> None:
        """Кончились варианты — отказываемся, а не кладём поверх."""
        source = self._file(tmp_path / "source.mp4", 10)
        destination = self._file(tmp_path / "Show S01E01.mp4", 999)
        for variant in range(2, 21):
            self._file(tmp_path / f"Show S01E01 v{variant}.mp4", 998)

        with pytest.raises(QueueError):
            free_destination(destination, source)

    def test_the_suffix_stays_at_the_end(self, tmp_path) -> None:
        """Пометка идёт перед расширением: иначе файл перестанет быть `.ass`."""
        source = self._file(tmp_path / "source.ass", 10)
        destination = self._file(tmp_path / "Show S01E01.ru.ass", 999)

        assert free_destination(destination, source).name == "Show S01E01.ru v2.ass"
