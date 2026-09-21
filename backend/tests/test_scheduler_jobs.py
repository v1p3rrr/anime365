"""Планировщик: джобы не стартуют в одну секунду, а помеха переносит, не отменяет.

На сервере пользователя все интервалы отсчитывались от секунды старта и раз в
сутки сходились: полный рескан падал с «уже идёт», сбор переводов и повтор
автоскачивания откладывались до завтра ([D-212](../../claude/09-decisions.md)).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.workers import scheduler as jobs


class TestStagger:
    def test_first_runs_are_spread_out(self) -> None:
        offsets = [0, 5, 10, 15, 20, 30, 40]
        starts = [jobs._every(24, minutes).start_date for minutes in offsets]

        assert starts == sorted(starts)
        assert len(set(starts)) == len(starts), "две джобы с одним стартом — снова столкновение"

    def test_the_offset_is_minutes_from_now(self) -> None:
        zero = jobs._every(1, 0).start_date
        later = jobs._every(1, 30).start_date

        assert 29 * 60 <= (later - zero).total_seconds() <= 31 * 60


class TestRetrySoon:
    @staticmethod
    def _started(monkeypatch) -> AsyncIOScheduler:
        # Запущенный, но на паузе: у незапущенного планировщика задачи лежат
        # в списке ожидания, и `modify_job` их не находит — в проде перенос
        # ставится из работающего.
        loop = asyncio.new_event_loop()
        scheduler = AsyncIOScheduler(timezone="UTC", event_loop=loop)
        scheduler.start(paused=True)
        monkeypatch.setattr(jobs, "_scheduler", scheduler)
        return scheduler

    def test_the_same_job_is_moved_not_duplicated(self, monkeypatch) -> None:
        """Вторая одноразовая джоба обошла бы `max_instances=1` и пошла бы параллельно штатной."""
        scheduler = self._started(monkeypatch)
        try:

            async def job() -> None:
                pass

            scheduler.add_job(job, jobs._every(24, 15), id="translation_scan", max_instances=1)
            before = scheduler.get_job("translation_scan").next_run_time

            jobs._retry_soon("translation_scan", "сбор сведений о переводах")

            assert [j.id for j in scheduler.get_jobs()] == ["translation_scan"], (
                "джоба одна, с тем же id"
            )
            moved = scheduler.get_job("translation_scan").next_run_time
            assert moved < before, "перенесено ближе, а не на следующий интервал"
            assert isinstance(scheduler.get_job("translation_scan").trigger, IntervalTrigger)
            assert 9 * 60 <= (moved - datetime.now(UTC)).total_seconds() <= 11 * 60
        finally:
            scheduler.shutdown(wait=False)

    def test_a_job_missing_from_the_schedule_is_only_logged(self, monkeypatch) -> None:
        """Джоба выключена настройкой, а вызвали руками — переносить нечего."""
        scheduler = self._started(monkeypatch)
        try:
            jobs._retry_soon("translation_scan", "сбор сведений о переводах")
            assert scheduler.get_jobs() == []
        finally:
            scheduler.shutdown(wait=False)

    def test_without_a_scheduler_it_only_logs(self, monkeypatch) -> None:
        monkeypatch.setattr(jobs, "_scheduler", None)

        jobs._retry_soon("stream_probe", "определение дорожек")
