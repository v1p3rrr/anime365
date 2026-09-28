"""Планировщик: джобы не стартуют в одну секунду, а помеха переносит, не отменяет.

На сервере пользователя все интервалы отсчитывались от секунды старта и раз в
сутки сходились: полный рескан падал с «уже идёт», сбор переводов и повтор
автоскачивания откладывались до завтра ([D-212](../../claude/09-decisions.md)).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
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


@pytest.fixture
def started(monkeypatch):
    """Планировщик запущенный, но на паузе, подставленный модулю.

    У незапущенного задачи лежат в списке ожидания, и `modify_job` их не
    находит — в проде перенос ставится из работающего. Цикл событий свой и
    закрывается здесь же, иначе тест течёт незакрытым циклом.
    """
    loop = asyncio.new_event_loop()
    scheduler = AsyncIOScheduler(timezone="UTC", event_loop=loop)
    scheduler.start(paused=True)
    monkeypatch.setattr(jobs, "_scheduler", scheduler)
    try:
        yield scheduler
    finally:
        scheduler.shutdown(wait=False)
        loop.close()


class TestRetrySoon:
    def test_the_same_job_is_moved_not_duplicated(self, started) -> None:
        """Вторая одноразовая джоба обошла бы `max_instances=1` и пошла бы параллельно штатной."""

        async def job() -> None:
            pass

        started.add_job(job, jobs._every(24, 15), id="translation_scan", max_instances=1)
        before = started.get_job("translation_scan").next_run_time

        jobs._retry_soon("translation_scan", "сбор сведений о переводах")

        assert [j.id for j in started.get_jobs()] == ["translation_scan"], "джоба одна, с тем же id"
        moved = started.get_job("translation_scan").next_run_time
        assert moved < before, "перенесено ближе, а не на следующий интервал"
        assert isinstance(started.get_job("translation_scan").trigger, IntervalTrigger)
        assert 9 * 60 <= (moved - datetime.now(UTC)).total_seconds() <= 11 * 60

    def test_a_job_missing_from_the_schedule_is_only_logged(self, started) -> None:
        """Джоба выключена настройкой, а вызвали руками — переносить нечего."""
        jobs._retry_soon("translation_scan", "сбор сведений о переводах")

        assert started.get_jobs() == []

    def test_without_a_scheduler_it_only_logs(self, monkeypatch) -> None:
        monkeypatch.setattr(jobs, "_scheduler", None)

        jobs._retry_soon("stream_probe", "определение дорожек")


class TestSuiteDoesNotSyncTheLiveCatalog:
    def test_app_under_tests_starts_the_scheduler_paused(self, client) -> None:
        """Без паузы инкремент каталога шёл на живой сайт при каждом старте `client`.

        Шесть тысяч настоящих тайтлов в общей тестовой базе роняли
        `test_mapping_matrix` через раз — см. фикстуру `scheduler_paused`.
        """
        from apscheduler.schedulers.base import STATE_PAUSED

        assert jobs._scheduler is not None
        assert jobs._scheduler.state == STATE_PAUSED
        assert jobs._scheduler.get_job("catalog_incremental") is not None
