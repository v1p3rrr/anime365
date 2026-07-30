"""Тесты сводки переводов: диапазоны покрытия, выбор качества, подсчёт серий."""

from __future__ import annotations

import pytest

from app.services.coverage import (
    MAX_DOWNLOAD_HEIGHT,
    build_quality_summary,
    build_summary,
    effective_episode_total,
    format_ranges,
    resolve_height,
)


class TestFormatRanges:
    """ТЗ требует показывать покрытие как «с 1 по 19, с 22 по 25»."""

    def test_continuous_range(self) -> None:
        assert format_ranges([1, 2, 3, 4, 5]) == "1–5"

    def test_gaps_split_into_ranges(self) -> None:
        assert format_ranges(range(1, 20)) + "" == "1–19"
        assert format_ranges([*range(1, 20), *range(22, 26)]) == "1–19, 22–25"

    def test_single_number(self) -> None:
        assert format_ranges([7]) == "7"

    def test_unsorted_and_duplicated_input(self) -> None:
        assert format_ranges([3, 1, 2, 2, 3]) == "1–3"

    def test_empty(self) -> None:
        assert format_ranges([]) == ""

    def test_fractional_episode_breaks_range(self) -> None:
        # Серии вида 7.5 существуют и не должны склеиваться с соседями.
        assert format_ranges([6, 7, 7.5, 8]) == "6–7, 7.5, 8"

    def test_none_values_are_ignored(self) -> None:
        assert format_ranges([1, None, 2]) == "1–2"


class TestResolveHeight:
    def test_exact_match(self) -> None:
        assert resolve_height(1080, [1080, 720, 360]) == 1080

    def test_falls_back_to_lower(self) -> None:
        # Запросили 1080, а у перевода потолок 360 — качаем 360, а не падаем.
        assert resolve_height(1080, [360]) == 360

    def test_picks_highest_below_preferred(self) -> None:
        assert resolve_height(720, [1080, 480, 360]) == 480

    def test_non_standard_rungs(self) -> None:
        # Реальные случаи из API: 526 → [478], 396 → [396].
        assert resolve_height(1080, [478]) == 478
        assert resolve_height(720, [396]) == 396

    def test_all_above_preferred_gives_max(self) -> None:
        assert resolve_height(240, [1080, 720]) == 1080

    def test_empty_gives_none(self) -> None:
        assert resolve_height(1080, []) is None


class TestEpisodeTotal:
    def test_local_count_wins_when_larger(self) -> None:
        # По ТЗ: если локально 25 серий, а сайт знает про 22 из 24 — показываем 25.
        assert effective_episode_total(24, 22, 25) == 25

    def test_planned_wins_over_uploaded(self) -> None:
        # Онгоинг: заявлено 19, выложено 11.
        assert effective_episode_total(19, 11, 0) == 19

    def test_api_wins_when_plan_is_zero(self) -> None:
        assert effective_episode_total(0, 13, 0) == 13


class TestSummary:
    @staticmethod
    def _tr(tid: int, lang: str, kind: str, author: str, episode: float, height: int = 1080) -> dict:
        return {
            "id": tid,
            "type_lang": lang,
            "type_kind": kind,
            "authors_summary": author,
            "episode_num": episode,
            "height": height,
        }

    def test_other_languages_are_dropped(self) -> None:
        items = [
            self._tr(1, "ru", "voice", "A", 1),
            self._tr(2, "er", "voiceOth", "B", 1),
        ]
        summary = build_summary(items, total_episodes=1)
        assert [s["lang"] for s in summary] == ["ru"]

    def test_complete_coverage_is_marked(self) -> None:
        items = [self._tr(i, "ru", "sub", "AniRise", i) for i in range(1, 13)]
        summary = build_summary(items, total_episodes=12)
        group = summary[0]["groups"][0]
        assert group["is_complete"] is True
        assert group["coverage"] == "все серии"

    def test_partial_coverage_shows_ranges(self) -> None:
        items = [self._tr(i, "ru", "sub", "X", i) for i in [1, 2, 3, 7, 8]]
        group = build_summary(items, total_episodes=12)[0]["groups"][0]
        assert group["is_complete"] is False
        assert group["coverage"] == "1–3, 7–8"

    def test_complete_groups_come_first(self) -> None:
        items = [
            *[self._tr(100 + i, "ru", "sub", "Partial", i) for i in (1, 2)],
            *[self._tr(200 + i, "ru", "sub", "Full", i) for i in range(1, 5)],
        ]
        groups = build_summary(items, total_episodes=4)[0]["groups"]
        assert groups[0]["authors"] == "Full"

    def test_missing_author_gets_placeholder(self) -> None:
        group = build_summary([self._tr(1, "ru", "sub", "  ", 1)], 1)[0]["groups"][0]
        assert group["authors"] == "без указания автора"

    def test_language_order_is_stable(self) -> None:
        items = [
            self._tr(1, "ja", "raw", "R", 1),
            self._tr(2, "en", "sub", "E", 1),
            self._tr(3, "ru", "voice", "V", 1),
        ]
        assert [s["lang"] for s in build_summary(items, 1)] == ["ru", "en", "ja"]


class TestQualitySummary:
    @staticmethod
    def _tr(episode: float, height: int) -> dict:
        return {
            "id": int(episode * 10),
            "type_lang": "ru",
            "type_kind": "sub",
            "authors_summary": "A",
            "episode_num": episode,
            "height": height,
        }

    def test_uniform_quality(self) -> None:
        result = build_quality_summary([self._tr(i, 1080) for i in range(1, 13)], 12)
        assert result["uniform"] == 1080
        assert result["is_estimate"] is True

    def test_mixed_quality_splits_into_ranges(self) -> None:
        items = [*[self._tr(i, 1080) for i in range(1, 5)], *[self._tr(i, 720) for i in range(5, 7)]]
        result = build_quality_summary(items, 6)
        assert result["uniform"] is None
        assert result["buckets"][0] == {"height": 1080, "episodes": "1–4", "count": 4}
        assert result["buckets"][1]["episodes"] == "5–6"

    def test_4k_is_capped_to_download_ceiling(self) -> None:
        # Источники бывают 2160p, но скачать можно максимум 1080 — показывать 2160
        # значит обещать недоступное.
        result = build_quality_summary([self._tr(i, 2160) for i in range(1, 5)], 4)
        assert result["uniform"] == MAX_DOWNLOAD_HEIGHT
        assert result["capped_from_4k"] is True

    def test_episodes_without_data_are_counted(self) -> None:
        result = build_quality_summary([self._tr(1, 720)], total_episodes=12)
        assert result["unknown_episodes"] == 11

    def test_no_data_at_all(self) -> None:
        result = build_quality_summary([], total_episodes=5)
        assert result["buckets"] == []
        assert result["unknown_episodes"] == 5


@pytest.mark.parametrize(
    ("preferred", "available", "expected"),
    [(1080, [1080, 720, 360], 1080), (720, [1080, 720, 360], 720), (1080, [480, 360], 480)],
)
def test_resolve_height_table(preferred: int, available: list[int], expected: int) -> None:
    assert resolve_height(preferred, available) == expected


class TestExtraEpisodeSplit:
    """Спешлы и OVA лежат отдельно от основных серий.

    Без разделения тумблер «включая спешлы» в батче был декоративным: он менял
    своё положение, но в очередь всё равно уходил полный список переводов.
    """

    @staticmethod
    def _translation(tid: int, episode_type: str, episode_num: float) -> dict:
        return {
            "id": tid,
            "type_lang": "ru",
            "type_kind": "voice",
            "authors_summary": "Студия",
            "episode_num": episode_num,
            "episode_type": episode_type,
        }

    def test_specials_go_to_separate_list(self) -> None:
        from app.services.coverage import build_summary

        sections = build_summary(
            [
                self._translation(1, "tv", 1),
                self._translation(2, "tv", 2),
                self._translation(3, "special", 1),
                self._translation(4, "ova", 1),
            ],
            total_episodes=2,
        )
        group = sections[0]["groups"][0]
        assert group["translation_ids"] == [1, 2]
        assert group["extra_translation_ids"] == [3, 4]

    def test_count_reflects_main_episodes_only(self) -> None:
        from app.services.coverage import build_summary

        sections = build_summary(
            [self._translation(1, "tv", 1), self._translation(2, "special", 1)],
            total_episodes=1,
        )
        group = sections[0]["groups"][0]
        # «Переводов у автора» в батче означает основные серии: спешлы
        # добавляются только при включённом тумблере.
        assert group["count"] == 1

    def test_title_without_extras_has_empty_list(self) -> None:
        from app.services.coverage import build_summary

        sections = build_summary([self._translation(1, "tv", 1)], total_episodes=1)
        # Пустой список — сигнал UI не показывать тумблер вовсе.
        assert sections[0]["groups"][0]["extra_translation_ids"] == []


class TestQualityRefinement:
    """Точные ступени из embed перекрывают оценку по разрешению исходника.

    Оценка врёт в обе стороны: у источника 528p ступени бывают [720, 360],
    у 4K потолок всё равно 1080. Смешивать уточнённые и прикидочные цифры без
    пометки нельзя — пользователь примет их за факт.
    """

    @staticmethod
    def _translations(*pairs: tuple[float, int]) -> list[dict]:
        return [
            {"id": i, "episode_num": episode, "height": height, "type_lang": "ru"}
            for i, (episode, height) in enumerate(pairs, start=1)
        ]

    def test_without_exact_data_it_is_an_estimate(self) -> None:
        from app.services.coverage import build_quality_summary

        summary = build_quality_summary(self._translations((1, 1080), (2, 1080)), 2)
        assert summary["is_estimate"] is True
        assert summary["refined_episodes"] == 0

    def test_exact_overrides_estimate(self) -> None:
        from app.services.coverage import build_quality_summary

        # Источник 528p, а реально доступно 720p — оценка обязана уступить.
        summary = build_quality_summary(self._translations((1, 528)), 1, exact={1: [720, 360]})
        assert summary["uniform"] == 720
        assert summary["refined_episodes"] == 1
        assert summary["is_estimate"] is False

    def test_partial_refinement_stays_marked_as_estimate(self) -> None:
        from app.services.coverage import build_quality_summary

        summary = build_quality_summary(
            self._translations((1, 1080), (2, 720)), 2, exact={1: [1080, 720]}
        )
        # Уточнена одна серия из двух — сводка всё ещё частично прикидочная.
        assert summary["is_estimate"] is True
        assert summary["refined_episodes"] == 1

    def test_4k_source_is_still_capped(self) -> None:
        from app.services.coverage import build_quality_summary

        summary = build_quality_summary(self._translations((1, 2160)), 1)
        assert summary["uniform"] == 1080
        assert summary["capped_from_4k"] is True

    def test_empty_exact_list_does_not_override(self) -> None:
        from app.services.coverage import build_quality_summary

        # Пустой список ступеней — это «данных нет», а не «доступно 0p».
        summary = build_quality_summary(self._translations((1, 1080)), 1, exact={1: []})
        assert summary["uniform"] == 1080
