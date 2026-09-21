"""Планировщик: джобы не стартуют в одну секунду, а помеха переносит, не отменяет.

На сервере пользователя все интервалы отсчитывались от секунды старта и раз в
сутки сходились: полный рескан падал с «уже идёт», сбор переводов и повтор
автоскачивания откладывались до завтра ([D-212](../../claude/09-decisions.md)).
"""

from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

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
    def test_a_blocked_job_is_rearmed_once(self, monkeypatch) -> None:
        loop = asyncio.new_event_loop()
        # Запущенный, но на паузе: у незапущенного планировщика задачи лежат
        # в списке ожидания, и `replace_existing` там не действует — в проде
        # повтор ставится из работающего.
        scheduler = AsyncIOScheduler(timezone="UTC", event_loop=loop)
        scheduler.start(paused=True)
        try:
            monkeypatch.setattr(jobs, "_scheduler", scheduler)

            async def job() -> None:
                pass

            jobs._retry_soon("translation_scan", job, "сбор сведений о переводах")
            jobs._retry_soon("translation_scan", job, "сбор сведений о переводах")

            retries = [j for j in scheduler.get_jobs() if j.id == "translation_scan:retry"]
            assert len(retries) == 1, "второй конфликт переносит тот же повтор, а не плодит очередь"
            assert isinstance(retries[0].trigger, DateTrigger)
        finally:
            scheduler.shutdown(wait=False)
            loop.close()

    def test_without_a_scheduler_it_only_logs(self, monkeypatch) -> None:
        monkeypatch.setattr(jobs, "_scheduler", None)

        async def job() -> None:
            pass

        jobs._retry_soon("stream_probe", job, "определение дорожек")
