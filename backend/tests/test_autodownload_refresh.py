"""Автоскачивание: поход за переводами на сайт и снятие устаревших промахов.

Два дефекта одной истории (Grand Blue S3, сентябрь 2026):

* E12 скачалась через семь минут после того, как пользователь открыл страницу
  тайтла, а до этого проход её не видел — переводы на сайте были, в кэше нет.
  Кэш наполняли только клики, суточный сбор пишет одни счётчики;
* E10 шесть суток числилась «не найденной»: 13.09 файл заменили на релиз со
  вшитой русской дорожкой, проход отсеивал серию как `embedded`, а запись о
  промахе снималась только при постановке в очередь.

Помощники и очистка — те же, что в `test_autodownload_engine.py`.
"""

from __future__ import annotations

import asyncio
import pathlib
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.db import session_scope
from app.models.autodownload import AutoDownloadMiss, AutoDownloadRule
from app.models.catalog import Translation
from app.models.library import LibraryFile
from app.services import autodownload
from app.services.autodownload import plan_for_series, remember_misses, run_for_series
from tests.test_autodownload_engine import ROOT, SERIES, _sidecar, _translation, _video
from tests.test_autodownload_engine import clean as _clean  # noqa: F401 — autouse


@pytest.fixture
def soft_subtitles(monkeypatch):
    async def fake_embed(translation_id: int, **kwargs):
        return {"has_subtitles": True}

    monkeypatch.setattr("app.services.title_service.get_embed", fake_embed)


def _rule_all() -> None:
    with session_scope() as session:
        session.merge(AutoDownloadRule(lang="ru", mode="all", priorities=["kazoku"]))


def _synced(tid: int, *, hours_ago: float, missing: bool = False) -> None:
    with session_scope() as session:
        row = session.get(Translation, tid)
        row.synced_at = datetime.utcnow() - timedelta(hours=hours_ago)
        if missing:
            row.missing_since = datetime.utcnow()


class TestCacheRefresh:
    """Идём на сайт, только когда есть к чему качать и кэш не свежий."""

    def test_empty_cache_with_a_missing_episode_goes_to_the_site(self, site_refresh) -> None:
        """Тот самый случай: серия лежит, переводов в кэше нет вовсе."""
        _video(1)
        _rule_all()

        _, misses = asyncio.run(plan_for_series(SERIES))

        assert site_refresh.calls == [SERIES]
        # Поход не удался (заглушка отвечает отказом) — считаем по кэшу, как
        # раньше: промах записан, проход не упал.
        assert misses == [(1.0, "ru")]

    def test_a_stale_cache_is_refreshed_once_per_pass(self, site_refresh, soft_subtitles) -> None:
        _video(1)
        _video(2)
        _translation(101, 1, "Kazoku")
        _synced(101, hours_ago=7)
        _rule_all()

        asyncio.run(plan_for_series(SERIES))

        assert site_refresh.calls == [SERIES], "две недостающие серии — один поход"

    def test_a_fresh_cache_is_left_alone(self, site_refresh, soft_subtitles) -> None:
        """Страница тайтла только что обновила кэш — лишний запрос ничего не даст."""
        _video(1)
        _translation(101, 1, "Kazoku")
        _synced(101, hours_ago=1)
        _rule_all()

        plan, _ = asyncio.run(plan_for_series(SERIES))

        assert site_refresh.calls == []
        assert [choice.translation_id for choice in plan] == [101]

    def test_nothing_missing_means_no_trip(self, site_refresh) -> None:
        _video(1)
        _sidecar(1, "ru")
        _rule_all()

        asyncio.run(plan_for_series(SERIES))

        assert site_refresh.calls == []

    def test_a_vanished_translation_does_not_make_the_cache_stale(
        self, site_refresh, soft_subtitles
    ) -> None:
        """Пометка «пропал с сайта» не обновляет `synced_at`.

        У Mushoku Tensei III таких три, и по общей проверке свежести тайтл ходил
        бы на сайт каждый час до конца времён.
        """
        _video(1)
        _translation(101, 1, "Kazoku")
        _synced(101, hours_ago=1)
        _translation(102, 1, "Gone")
        _synced(102, hours_ago=200, missing=True)
        _rule_all()

        asyncio.run(plan_for_series(SERIES))

        assert site_refresh.calls == []

    def test_the_trip_reads_episodes_before_translations(self, site_refresh, monkeypatch) -> None:
        """Перевод привязан к серии по `episode_id`: без строки серии он не кандидат."""
        order: list[str] = []

        async def fake_details(series_id: int, force: bool = False):
            order.append(f"details:{series_id}:{force}")

        async def fake_translations(series_id: int, force: bool = False):
            order.append(f"translations:{series_id}:{force}")
            return []

        monkeypatch.setattr("app.services.title_service.ensure_details", fake_details)
        monkeypatch.setattr("app.services.title_service.ensure_translations", fake_translations)

        assert asyncio.run(site_refresh.original(SERIES)) is True
        assert order == [f"details:{SERIES}:True", f"translations:{SERIES}:True"]

    def test_a_failed_trip_is_a_warning_not_a_crash(self, site_refresh, monkeypatch) -> None:
        async def boom(series_id: int, force: bool = False):
            raise RuntimeError("site is down")

        monkeypatch.setattr("app.services.title_service.ensure_details", boom)

        assert asyncio.run(site_refresh.original(SERIES)) is False

    def test_one_failure_backs_off_that_title_only(self, site_refresh, monkeypatch) -> None:
        """Один отказ бывает и у здорового сайта — соседи по проходу не виноваты."""
        attempts: list[int] = []

        async def boom(series_id: int, force: bool = False):
            attempts.append(series_id)
            raise RuntimeError("bad record")

        monkeypatch.setattr("app.services.title_service.ensure_details", boom)

        assert asyncio.run(site_refresh.original(SERIES)) is False
        assert asyncio.run(site_refresh.original(SERIES)) is False
        assert asyncio.run(site_refresh.original(SERIES + 1)) is False
        assert attempts == [SERIES, SERIES + 1], "неудачник ждёт, сосед идёт"

    def test_three_failures_in_a_row_pause_everyone(self, site_refresh, monkeypatch) -> None:
        """Три отказа подряд на разных тайтлах — это сайт, а не запись."""
        attempts: list[int] = []

        async def boom(series_id: int, force: bool = False):
            attempts.append(series_id)
            raise RuntimeError("site is down")

        monkeypatch.setattr("app.services.title_service.ensure_details", boom)

        for offset in range(4):
            assert asyncio.run(site_refresh.original(SERIES + offset)) is False

        assert attempts == [SERIES, SERIES + 1, SERIES + 2], "четвёртый в паузу не ходит"
        assert autodownload._refresh_paused_until is not None

    def test_a_success_resets_the_failure_streak(self, site_refresh, monkeypatch) -> None:
        calls: list[str] = []

        async def flaky_details(series_id: int, force: bool = False):
            calls.append(f"details:{series_id}")
            if series_id != SERIES + 1:
                raise RuntimeError("bad record")

        async def fine(series_id: int, force: bool = False):
            return []

        monkeypatch.setattr("app.services.title_service.ensure_details", flaky_details)
        monkeypatch.setattr("app.services.title_service.ensure_translations", fine)

        assert asyncio.run(site_refresh.original(SERIES)) is False
        assert asyncio.run(site_refresh.original(SERIES + 1)) is True
        assert asyncio.run(site_refresh.original(SERIES + 2)) is False
        assert asyncio.run(site_refresh.original(SERIES + 3)) is False

        # Отказ, успех, два отказа: без сброса это были бы три подряд и пауза.
        assert calls == [f"details:{SERIES + offset}" for offset in range(4)]
        assert autodownload._refresh_paused_until is None, "серия прервана успехом"

    def test_the_pause_ends(self, site_refresh, monkeypatch) -> None:
        attempts: list[int] = []

        async def fine(series_id: int, force: bool = False):
            attempts.append(series_id)
            return []

        monkeypatch.setattr("app.services.title_service.ensure_details", fine)
        monkeypatch.setattr("app.services.title_service.ensure_translations", fine)
        monkeypatch.setattr(
            autodownload, "_refresh_paused_until", datetime.utcnow() - timedelta(seconds=1)
        )

        assert asyncio.run(site_refresh.original(SERIES)) is True
        assert attempts == [SERIES, SERIES]
        assert autodownload._refresh_paused_until is None, "успех снимает паузу"

    def test_the_title_backoff_ends(self, site_refresh, monkeypatch) -> None:
        attempts: list[int] = []

        async def fine(series_id: int, force: bool = False):
            attempts.append(series_id)
            return []

        monkeypatch.setattr("app.services.title_service.ensure_details", fine)
        monkeypatch.setattr("app.services.title_service.ensure_translations", fine)
        monkeypatch.setattr(
            autodownload,
            "_failed_series",
            {SERIES: datetime.utcnow() - autodownload.REFRESH_PAUSE - timedelta(seconds=1)},
        )

        assert asyncio.run(site_refresh.original(SERIES)) is True
        assert attempts == [SERIES, SERIES]
        assert SERIES not in autodownload._failed_series, "успех снимает отступ тайтла"

    def test_a_page_refresh_in_flight_is_not_doubled(self, site_refresh, monkeypatch) -> None:
        """Страница тайтла уже обновляет кэш фоном — второй поход за тем же не нужен."""
        attempts: list[int] = []

        async def fine(series_id: int, force: bool = False):
            attempts.append(series_id)

        monkeypatch.setattr("app.services.title_service.ensure_details", fine)
        monkeypatch.setattr("app.services.title_service.translations_pending", lambda sid: True)

        assert asyncio.run(site_refresh.original(SERIES)) is False
        assert attempts == []

    def test_a_deleted_video_is_not_worth_the_trip(self, site_refresh) -> None:
        """Индекс ещё помнит файл, диска уже нет — качать не к чему, идти незачем."""
        _video(1)
        pathlib.Path(ROOT, "Autodownload", "Show S01E01.mkv").unlink()
        _rule_all()

        asyncio.run(plan_for_series(SERIES))

        assert site_refresh.calls == []

    def test_subtitles_put_by_hand_are_not_worth_the_trip(self, site_refresh) -> None:
        """Файл рядом уже лежит, просто скан его не видел."""
        _video(1)
        pathlib.Path(ROOT, "Autodownload", "Show S01E01.ru.ass").write_text("x", encoding="utf-8")
        _rule_all()

        asyncio.run(plan_for_series(SERIES))

        assert site_refresh.calls == []

    def test_a_dismissed_pair_alone_does_not_trigger_the_trip(self, site_refresh) -> None:
        """Выкинутое вручную — не недостающее: автоматический проход мимо."""
        file_id = _video(1)
        with session_scope() as session:
            # Появился час назад: отстоялся, в окне, правило без даты включения.
            session.get(LibraryFile, file_id).mtime = time.time() - 3600
        _miss(1.0, dismissed=True)
        _rule_all()

        asyncio.run(run_for_series(SERIES, automatic=True))

        assert site_refresh.calls == []


def _miss(episode: float = 1.0, *, dismissed: bool = False) -> None:
    remember_misses(SERIES, [(episode, "ru")])
    if dismissed:
        with session_scope() as session:
            row = session.execute(
                select(AutoDownloadMiss).where(
                    AutoDownloadMiss.series_id == SERIES,
                    AutoDownloadMiss.episode == episode,
                )
            ).scalar_one()
            row.dismissed = True


def _misses() -> list[tuple[float, bool]]:
    with session_scope() as session:
        rows = session.execute(
            select(AutoDownloadMiss.episode, AutoDownloadMiss.dismissed)
            .where(AutoDownloadMiss.series_id == SERIES)
            .order_by(AutoDownloadMiss.episode)
        ).all()
    return [(float(episode), bool(dismissed)) for episode, dismissed in rows]


class TestSettledMissIsCleared:
    """Серия перестала быть недостающей не через нас — промах ей больше не нужен."""

    def test_an_embedded_track_clears_the_record(self) -> None:
        _miss()
        _video(1, subtitle_languages=["rus"], probed=True)
        _rule_all()

        asyncio.run(run_for_series(SERIES))

        assert _misses() == []

    def test_a_sidecar_clears_it_too(self) -> None:
        _miss()
        _video(1)
        _sidecar(1, "ru")
        _rule_all()

        asyncio.run(run_for_series(SERIES))

        assert _misses() == []

    def test_a_dismissed_record_stays(self) -> None:
        """«Выкинул» значит «перестань этим заниматься» — и запись это охраняет."""
        _miss(dismissed=True)
        _video(1, subtitle_languages=["rus"], probed=True)
        _rule_all()

        asyncio.run(run_for_series(SERIES))

        assert _misses() == [(1.0, True)]

    def test_a_still_missing_episode_keeps_its_record(self) -> None:
        _miss()
        _video(1)
        _rule_all()

        asyncio.run(run_for_series(SERIES))

        assert _misses() == [(1.0, False)]

    def test_out_of_scope_is_not_judged(self) -> None:
        """Автоматический проход старый файл не смотрел — и решать за него не вправе."""
        _miss()
        # `_video` кладёт mtime=1.0: файл из 1970 года, вне любого окна.
        _video(1, subtitle_languages=["rus"], probed=True)
        _rule_all()

        asyncio.run(run_for_series(SERIES, automatic=True))

        assert _misses() == [(1.0, False)]

    def test_a_thousand_episode_title_does_not_break_the_cleanup(self) -> None:
        """One Piece с дорожками внутри — две тысячи закрытых пар за проход.

        `OR` из стольких условий упирался в предел глубины выражения SQLite,
        и проход по тайтлу падал до постановки в очередь.
        """
        from app.services.autodownload import clear_settled_misses

        _miss(1.0)
        _miss(2.0, dismissed=True)
        _miss(3.0)
        settled = {(float(n), lang) for n in range(1, 1101) for lang in ("ru", "en")}
        settled.discard((3.0, "ru"))

        clear_settled_misses(SERIES, settled)

        assert _misses() == [(2.0, True), (3.0, False)]

    def test_only_narrows_the_cleanup_as_well(self) -> None:
        """Повтор промахов смотрит на свои пары — и чистит только их."""
        _miss(1.0)
        _miss(2.0)
        _video(1, subtitle_languages=["rus"], probed=True)
        _video(2, subtitle_languages=["rus"], probed=True)
        _rule_all()

        asyncio.run(run_for_series(SERIES, only={(2.0, "ru")}))

        assert _misses() == [(1.0, False)]
