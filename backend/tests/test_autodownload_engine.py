"""Подбор субтитров для автоскачивания: к чему качаем и чей перевод берём.

Два правила здесь заданы пользователем и потому проверяются придирчиво:

* качаем **только к сериям, у которых на диске есть видео** — субтитры без
  видео никому не нужны, а у тайтла, добавленного «на будущее», их набежали бы
  сотни;
* **не знаем — качаем**. Данные о вшитых дорожках частичные (`.nfo` есть не у
  всех, ffprobe опционален) и местами неверные, поэтому молчать из-за
  неизвестности нельзя.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.autodownload import AutoDownloadRule, SeriesAutoDownload
from app.models.catalog import Episode, Series, Translation
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, LibraryRoot
from app.services.autodownload import (
    episodes_with_video,
    missing_episodes,
    plan_for_series,
    rank_candidates,
)
from app.services.autodownload_rules import Resolved, resolve_for_series

SERIES = 960_001
ROOT = "/test/autodownload"


@pytest.fixture(autouse=True)
def clean():
    def wipe() -> None:
        with session_scope() as session:
            session.execute(delete(AutoDownloadRule))
            session.execute(
                delete(SeriesAutoDownload).where(SeriesAutoDownload.series_id == SERIES)
            )
            session.execute(delete(EpisodeMapping).where(EpisodeMapping.series_id == SERIES))
            session.execute(delete(Translation).where(Translation.series_id == SERIES))
            session.execute(delete(Episode).where(Episode.series_id == SERIES))
            root = session.execute(
                select(LibraryRoot).where(LibraryRoot.path == ROOT)
            ).scalar_one_or_none()
            if root is not None:
                session.execute(delete(LibraryFolder).where(LibraryFolder.root_id == root.id))

    wipe()
    with session_scope() as session:
        session.merge(Series(id=SERIES, title="Autodownload Test", type="tv"))
    yield
    wipe()


def _folder() -> int:
    """Одна папка на тест: файлы тайтла лежат рядом, а не в разных."""
    with session_scope() as session:
        root = session.execute(
            select(LibraryRoot).where(LibraryRoot.path == ROOT)
        ).scalar_one_or_none()
        if root is None:
            root = LibraryRoot(path=ROOT, kind="series")
            session.add(root)
            session.flush()
        folder = session.execute(
            select(LibraryFolder).where(
                LibraryFolder.root_id == root.id, LibraryFolder.rel_path == "Autodownload"
            )
        ).scalar_one_or_none()
        if folder is None:
            folder = LibraryFolder(
                root_id=root.id, rel_path="Autodownload", name="Autodownload"
            )
            session.add(folder)
            session.flush()
        return folder.id


def _video(
    episode: float, *, subtitle_languages: list[str] | None = None, probed: bool = False
) -> int:
    """Видеофайл серии. `subtitle_languages=None` — про дорожки ничего не знаем.

    Возвращает id файла: искать его потом по имени нельзя — имена в тестах
    повторяются, и запрос цепляет строки соседних проверок.
    """
    folder_id = _folder()
    with session_scope() as session:
        streams = None
        if probed:
            streams = {"audio_languages": ["jpn"], "subtitle_languages": subtitle_languages or []}
        row = LibraryFile(
            folder_id=folder_id,
            filename=f"Show S01E{int(episode):02d}.mkv",
            kind="video",
            size_bytes=100,
            mtime=1.0,
            episode=episode,
            streams=streams,
        )
        session.add(row)
        session.flush()
        session.add(
            EpisodeMapping(series_id=SERIES, episode=episode, file_id=row.id, role="video")
        )
        return row.id


def _sidecar(episode: float, lang: str) -> None:
    folder_id = _folder()
    with session_scope() as session:
        row = LibraryFile(
            folder_id=folder_id,
            filename=f"Show S01E{int(episode):02d}.{lang}.ass",
            kind="subtitle",
            lang=lang,
            size_bytes=10,
            mtime=1.0,
            episode=episode,
        )
        session.add(row)
        session.flush()
        session.add(
            EpisodeMapping(
                series_id=SERIES, episode=episode, file_id=row.id, role=f"sub_{lang}"
            )
        )


def _translation(tid: int, episode: float, author: str, lang: str = "ru", priority: int = 0):
    with session_scope() as session:
        episode_id = tid * 10
        session.merge(
            Episode(
                id=episode_id, series_id=SERIES, episode_num=episode, episode_type="tv"
            )
        )
        session.merge(
            Translation(
                id=tid,
                series_id=SERIES,
                episode_id=episode_id,
                type_kind="sub",
                type_lang=lang,
                authors_summary=author,
                priority=priority,
                is_active=True,
            )
        )


def _rule(**kwargs) -> Resolved:
    return Resolved(lang=kwargs.pop("lang", "ru"), **kwargs)


class TestOnlyWhereVideoExists:
    """Требование пользователя: субтитры без видео не качаем."""

    def test_a_title_without_files_needs_nothing(self) -> None:
        assert missing_episodes(SERIES, _rule(priorities=["kazoku"])) == []

    def test_only_downloaded_episodes_count(self) -> None:
        _video(1)
        _video(3)

        assert missing_episodes(SERIES, _rule(priorities=["kazoku"])) == [1.0, 3.0]

    def test_a_file_not_bound_to_an_episode_does_not_count(self) -> None:
        """Мусор и трейлеры лежат в папке, но серией не являются."""
        folder_id = _folder()
        with session_scope() as session:
            session.add(
                LibraryFile(
                    folder_id=folder_id,
                    filename="Trailer.mkv",
                    kind="video",
                    size_bytes=1,
                    mtime=1.0,
                )
            )

        assert episodes_with_video(SERIES) == {}


class TestWhatCountsAsAlreadyHaving:
    def test_a_sidecar_of_that_language_stops_us(self) -> None:
        _video(1)
        _sidecar(1, "ru")

        assert missing_episodes(SERIES, _rule(priorities=["kazoku"])) == []

    def test_a_sidecar_of_another_language_does_not(self) -> None:
        _video(1)
        _sidecar(1, "en")

        assert missing_episodes(SERIES, _rule(priorities=["kazoku"])) == [1.0]

    def test_an_embedded_track_stops_us(self) -> None:
        _video(1, subtitle_languages=["rus"], probed=True)

        assert missing_episodes(SERIES, _rule(priorities=["kazoku"])) == []

    def test_unknown_tracks_mean_we_download(self) -> None:
        """Главное правило: не знаем — качаем."""
        _video(1)  # про дорожки ничего не известно

        assert missing_episodes(SERIES, _rule(priorities=["kazoku"])) == [1.0]

    def test_probed_without_subtitles_means_we_download(self) -> None:
        """Проверяли и не нашли — это знание, и оно за скачивание."""
        _video(1, subtitle_languages=[], probed=True)

        assert missing_episodes(SERIES, _rule(priorities=["kazoku"])) == [1.0]

    def test_ignore_embedded_downloads_anyway(self) -> None:
        """Лазейка для случая, когда теги дорожек врут."""
        _video(1, subtitle_languages=["rus"], probed=True)

        assert missing_episodes(
            SERIES, _rule(priorities=["kazoku"], ignore_embedded=True)
        ) == [1.0]

    def test_a_sidecar_with_unknown_language_does_not_stop_us(self) -> None:
        """Язык файла неизвестен — значит неизвестно и то, есть ли нужный."""
        _video(1)
        folder_id = _folder()
        with session_scope() as session:
            row = LibraryFile(
                folder_id=folder_id,
                filename="Show S01E01.srt",
                kind="subtitle",
                size_bytes=10,
                mtime=1.0,
                episode=1,
            )
            session.add(row)
            session.flush()
            session.add(
                EpisodeMapping(
                    series_id=SERIES, episode=1, file_id=row.id, role="sub_unknown"
                )
            )

        assert missing_episodes(SERIES, _rule(priorities=["kazoku"])) == [1.0]


class TestRanking:
    ITEMS = [
        {"id": 1, "key": "kazoku", "authors": "Kazoku", "priority": 0},
        {"id": 2, "key": "wakanim", "authors": "Wakanim", "priority": 0},
        {"id": 3, "key": "неизвестные", "authors": "Неизвестные", "priority": 5},
    ]

    def test_the_list_sets_the_order(self) -> None:
        ranked = rank_candidates(self.ITEMS, _rule(priorities=["wakanim", "kazoku"]))

        assert [item["id"] for item in ranked] == [2, 1]

    def test_groups_outside_the_list_are_dropped(self) -> None:
        """Список — точный фильтр, пока не разрешено обратное."""
        ranked = rank_candidates(self.ITEMS, _rule(priorities=["wakanim"]))

        assert [item["id"] for item in ranked] == [2]

    def test_fallback_adds_the_rest_only_when_the_list_is_empty_handed(self) -> None:
        ranked = rank_candidates(
            self.ITEMS, _rule(priorities=["никого"], allow_any_fallback=True)
        )

        # Из списка никого — идут остальные, по оценке сайта.
        assert [item["id"] for item in ranked] == [3, 1, 2]

    def test_fallback_does_not_fire_while_the_list_matched(self) -> None:
        """Иначе «фолбек» превратился бы в «взять всех подряд»."""
        ranked = rank_candidates(
            self.ITEMS, _rule(priorities=["kazoku"], allow_any_fallback=True)
        )

        assert [item["id"] for item in ranked] == [1]

    def test_allow_any_ignores_the_list_entirely(self) -> None:
        """Второй переключатель: список гасится, но не стирается."""
        resolved = _rule(priorities=["kazoku"], allow_any=True)
        ranked = rank_candidates(self.ITEMS, resolved)

        assert [item["id"] for item in ranked] == [3, 1, 2]
        assert resolved.priorities == ["kazoku"], "значения списка должны сохраниться"

    def test_order_is_stable_for_equal_candidates(self) -> None:
        """Без второго ключа выдача прыгала бы между запросами."""
        same = [
            {"id": 7, "key": "a", "authors": "A", "priority": 0},
            {"id": 3, "key": "b", "authors": "B", "priority": 0},
        ]

        assert [item["id"] for item in rank_candidates(same, _rule(allow_any=True))] == [3, 7]


class TestPlan:
    """Полный проход: от настройки до списка «что качать»."""

    @staticmethod
    def _softsubs(monkeypatch) -> None:
        async def fake_embed(translation_id: int, **kwargs):
            return {"has_subtitles": True}

        monkeypatch.setattr("app.services.title_service.get_embed", fake_embed)

    def test_nothing_happens_while_autodownload_is_off(self, monkeypatch) -> None:
        self._softsubs(monkeypatch)
        _video(1)
        _translation(101, 1, "Kazoku")

        assert asyncio.run(plan_for_series(SERIES)) == []

    def test_a_chosen_translation_matches_the_priority_list(self, monkeypatch) -> None:
        self._softsubs(monkeypatch)
        _video(1)
        _translation(101, 1, "Kazoku")
        _translation(102, 1, "Wakanim")
        with session_scope() as session:
            session.merge(
                AutoDownloadRule(lang="ru", mode="all", priorities=["wakanim", "kazoku"])
            )

        plan = asyncio.run(plan_for_series(SERIES))

        assert [(c.episode, c.translation_id, c.reason) for c in plan] == [
            (1.0, 102, "priority")
        ]

    def test_hardsub_is_skipped_in_favour_of_the_next_one(self, monkeypatch) -> None:
        """Хардсаб отличим только через embed — и он нам не подходит."""

        async def fake_embed(translation_id: int, **kwargs):
            return {"has_subtitles": translation_id != 102}

        monkeypatch.setattr("app.services.title_service.get_embed", fake_embed)
        _video(1)
        _translation(101, 1, "Kazoku")
        _translation(102, 1, "Wakanim")
        with session_scope() as session:
            session.merge(
                AutoDownloadRule(lang="ru", mode="all", priorities=["wakanim", "kazoku"])
            )

        plan = asyncio.run(plan_for_series(SERIES))

        assert [c.translation_id for c in plan] == [101]

    def test_an_episode_without_candidates_is_simply_skipped(self, monkeypatch) -> None:
        self._softsubs(monkeypatch)
        _video(1)
        _video(2)
        _translation(101, 1, "Kazoku")
        with session_scope() as session:
            session.merge(AutoDownloadRule(lang="ru", mode="all", priorities=["kazoku"]))

        plan = asyncio.run(plan_for_series(SERIES))

        assert [c.episode for c in plan] == [1.0]

    def test_a_title_switched_off_gets_nothing(self, monkeypatch) -> None:
        self._softsubs(monkeypatch)
        _video(1)
        _translation(101, 1, "Kazoku")
        with session_scope() as session:
            session.merge(AutoDownloadRule(lang="ru", mode="all", priorities=["kazoku"]))
            session.add(SeriesAutoDownload(series_id=SERIES, lang="ru", mode="off"))

        assert asyncio.run(plan_for_series(SERIES)) == []
        assert resolve_for_series(SERIES) == {}


class TestAutomaticScope:
    """Автоматический проход берёт только то, что появилось при нём.

    Требование пользователя, и оно про доверие: включение автоскачивания не
    должно задним числом выкачивать субтитры ко всей библиотеке. На старое
    есть отдельная кнопка, и она этих ограничений не знает.
    """

    @staticmethod
    def _aged(episode: float, *, minutes: float = 0, days: float = 0) -> None:
        """Видеофайл, появившийся столько-то времени назад."""
        import time

        file_id = _video(episode)
        with session_scope() as session:
            session.get(LibraryFile, file_id).mtime = time.time() - minutes * 60 - days * 86400

    @staticmethod
    def _rule_since(days_ago: float) -> Resolved:
        from datetime import datetime, timedelta

        return _rule(
            priorities=["kazoku"],
            enabled_at=datetime.utcnow() - timedelta(days=days_ago),
        )

    def test_a_file_from_before_enabling_is_skipped(self) -> None:
        """Тот самый случай: файлы лежали, автоскачивание включили потом."""
        self._aged(1, days=10)

        assert missing_episodes(SERIES, self._rule_since(1), automatic=True) == []

    def test_the_button_takes_it_anyway(self) -> None:
        """Кнопка «искать по всей библиотеке» границы не знает."""
        self._aged(1, days=10)

        assert missing_episodes(SERIES, self._rule_since(1)) == [1.0]

    def test_a_file_that_arrived_after_enabling_is_taken(self) -> None:
        self._aged(1, days=1)

        assert missing_episodes(SERIES, self._rule_since(5), automatic=True) == [1.0]

    def test_a_fresh_file_has_to_settle_first(self) -> None:
        """Файл может ещё копироваться, а субтитры к нему — приехать следом."""
        self._aged(1, minutes=2)

        assert missing_episodes(SERIES, self._rule_since(5), automatic=True) == []

    def test_after_settling_it_is_taken(self) -> None:
        self._aged(1, minutes=90)

        assert missing_episodes(SERIES, self._rule_since(5), automatic=True) == [1.0]

    def test_a_long_unanswered_episode_stops_being_rechecked(self) -> None:
        """Перевода нет третий месяц — спрашивать сайт вечно незачем."""
        self._aged(1, days=200)

        assert missing_episodes(SERIES, self._rule_since(365), automatic=True) == []

    def test_without_an_enabling_mark_only_the_recheck_window_applies(self) -> None:
        """Старые записи отметки не имеют — ограничивать их этой границей нечем."""
        self._aged(1, days=1)

        assert missing_episodes(SERIES, _rule(priorities=["kazoku"]), automatic=True) == [1.0]


class TestWholeLibraryPass:
    def test_nothing_runs_while_every_language_is_off(self, monkeypatch) -> None:
        """Дешёвая проверка до обхода: выключено — значит не ходим никуда."""
        _video(1)
        _translation(101, 1, "Kazoku")

        result = asyncio.run(__import__(
            "app.services.autodownload", fromlist=["run_for_all"]
        ).run_for_all())

        assert result["queued"] == 0
        assert "skipped" in result

    def test_a_failing_title_does_not_stop_the_rest(self, monkeypatch) -> None:
        """Сайт может не ответить по любому переводу — проход обязан продолжиться."""
        from app.services import autodownload as engine

        _video(1)
        _translation(101, 1, "Kazoku")
        with session_scope() as session:
            session.merge(AutoDownloadRule(lang="ru", mode="all", priorities=["kazoku"]))

        async def explode(series_id: int, *args, **kwargs):
            raise RuntimeError("сайт не ответил")

        monkeypatch.setattr(engine, "run_for_series", explode)

        # Падение внутри тайтла не должно поднимать исключение наружу.
        assert asyncio.run(engine.run_for_all())["queued"] == 0
