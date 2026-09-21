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
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.db import session_scope
from app.models.autodownload import AutoDownloadMiss, AutoDownloadRule
from app.models.catalog import Translation
from app.services.autodownload import plan_for_series, remember_misses, run_for_series
from tests.test_autodownload_engine import SERIES, _sidecar, _translation, _video
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

    def test_only_narrows_the_cleanup_as_well(self) -> None:
        """Повтор промахов смотрит на свои пары — и чистит только их."""
        _miss(1.0)
        _miss(2.0)
        _video(1, subtitle_languages=["rus"], probed=True)
        _video(2, subtitle_languages=["rus"], probed=True)
        _rule_all()

        asyncio.run(run_for_series(SERIES, only={(2.0, "ru")}))

        assert _misses() == [(1.0, False)]
