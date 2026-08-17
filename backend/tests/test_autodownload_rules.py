"""Сложение глобальной настройки автоскачивания с настройкой тайтла.

Считается отдельно для каждого языка — это решение пользователя и не
косметика: русские субтитры можно хотеть всем подряд, английские только там,
где включил сам, а японские не качать вовсе.

Таблица участия проверяется целиком, всеми девятью сочетаниями. Это тот
случай, где «очевидно же» стоит дорого: пропущенная клетка означает либо
молчаливое нескачивание, либо скачивание там, где запретили.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.core.db import session_scope
from app.models.autodownload import AutoDownloadRule, SeriesAutoDownload
from app.services.autodownload_rules import (
    any_language_enabled,
    configured_series_ids,
    resolve_for_series,
)

SERIES = 970_001
OTHER = 970_002


@pytest.fixture(autouse=True)
def clean():
    """Настройки глобальные, поэтому между тестами их надо убирать.

    ⚠️ Тайтлы заводятся здесь же, и это не украшение. У `series_autodownload`
    внешний ключ на `series`, поэтому без строки тайтла вставка падает с
    `FOREIGN KEY constraint failed`. На машине разработчика этого не видно:
    файл тестовой БД лежит во временной папке и переживает прогоны, так что
    тайтлы оставались от соседних тестов. На **чистой** базе (свежий
    контейнер, CI) в этом файле падал двадцать один тест — ровно та ловушка,
    про которую предупреждает `conftest._schema`.
    """
    from app.models.catalog import Series

    def wipe() -> None:
        with session_scope() as session:
            session.execute(delete(AutoDownloadRule))
            session.execute(
                delete(SeriesAutoDownload).where(
                    SeriesAutoDownload.series_id.in_([SERIES, OTHER])
                )
            )

    wipe()
    with session_scope() as session:
        session.merge(Series(id=SERIES, title="Rules Test", type="tv"))
        session.merge(Series(id=OTHER, title="Rules Test 2", type="tv"))
    yield
    wipe()


def _globally(lang: str = "ru", mode: str = "all", **kwargs) -> None:
    with session_scope() as session:
        session.merge(
            AutoDownloadRule(
                lang=lang,
                mode=mode,
                priorities=kwargs.pop("priorities", ["wakanim"]),
                **kwargs,
            )
        )


def _for_title(mode: str = "default", series_id: int = SERIES, lang: str = "ru", **kwargs):
    with session_scope() as session:
        session.add(
            SeriesAutoDownload(series_id=series_id, lang=lang, mode=mode, **kwargs)
        )


class TestParticipationTable:
    """Все девять сочетаний. Пропущенная клетка тут стоит дороже, чем скучный тест."""

    @pytest.mark.parametrize(
        "global_mode,title_mode,expected",
        [
            ("off", "default", False),
            ("off", "on", False),
            ("off", "off", False),
            ("configured", "default", False),
            ("configured", "on", True),
            ("configured", "off", False),
            ("all", "default", True),
            ("all", "on", True),
            ("all", "off", False),
        ],
    )
    def test_cell(self, global_mode: str, title_mode: str, expected: bool) -> None:
        _globally(mode=global_mode)
        _for_title(mode=title_mode)

        assert ("ru" in resolve_for_series(SERIES)) is expected

    def test_disabled_language_cannot_be_switched_on_by_a_title(self) -> None:
        """Иначе «отключено» означало бы «отключено, кроме»."""
        _globally(mode="off")
        _for_title(mode="on")

        assert resolve_for_series(SERIES) == {}

    def test_a_title_can_always_opt_out(self) -> None:
        """Явный запрет весомее общего разрешения."""
        _globally(mode="all")
        _for_title(mode="off")

        assert resolve_for_series(SERIES) == {}


class TestLanguagesAreIndependent:
    def test_each_language_has_its_own_mode(self) -> None:
        _globally(lang="ru", mode="all")
        _globally(lang="en", mode="off")
        _globally(lang="ja", mode="configured")

        assert set(resolve_for_series(SERIES)) == {"ru"}

    def test_a_title_switches_one_language_without_touching_others(self) -> None:
        _globally(lang="ru", mode="all")
        _globally(lang="en", mode="all")
        _for_title(lang="en", mode="off")

        assert set(resolve_for_series(SERIES)) == {"ru"}

    def test_configured_mode_needs_the_matching_language(self) -> None:
        """Включили японский у тайтла — русский от этого не включается."""
        _globally(lang="ru", mode="configured")
        _globally(lang="ja", mode="configured")
        _for_title(lang="ja", mode="on")

        assert set(resolve_for_series(SERIES)) == {"ja"}


class TestInheritance:
    def test_an_empty_title_list_means_take_the_global_one(self) -> None:
        """Ровно то, что просил пользователь: «удалили свой — вернулся общий»."""
        _globally(priorities=["wakanim", "crunchyroll"])
        _for_title(mode="on", priorities=[])

        resolved = resolve_for_series(SERIES)["ru"]

        assert resolved.priorities == ["wakanim", "crunchyroll"]
        assert resolved.from_series is False

    def test_a_title_list_wins_over_the_global_one(self) -> None:
        _globally(priorities=["wakanim"])
        _for_title(mode="on", priorities=["kazoku"])

        resolved = resolve_for_series(SERIES)["ru"]

        assert resolved.priorities == ["kazoku"]
        assert resolved.from_series is True

    def test_unset_switches_fall_back_to_the_global_value(self) -> None:
        _globally(allow_any_fallback=True, ignore_embedded=True)
        _for_title(mode="on")

        resolved = resolve_for_series(SERIES)["ru"]

        assert resolved.allow_any_fallback is True
        assert resolved.ignore_embedded is True

    def test_a_title_can_turn_a_global_switch_off(self) -> None:
        """`False` у тайтла обязан отличаться от «не задано»."""
        _globally(allow_any_fallback=True)
        _for_title(mode="on", allow_any_fallback=False)

        assert resolve_for_series(SERIES)["ru"].allow_any_fallback is False

    def test_a_title_can_turn_a_global_switch_on(self) -> None:
        _globally(allow_any_fallback=False)
        _for_title(mode="on", allow_any_fallback=True)

        assert resolve_for_series(SERIES)["ru"].allow_any_fallback is True


class TestNothingToChooseFrom:
    """Настройка без единого кандидата — это не настройка."""

    def test_an_empty_list_without_any_permission_is_dropped(self) -> None:
        _globally(priorities=[])

        assert resolve_for_series(SERIES) == {}

    def test_allow_any_alone_is_enough(self) -> None:
        """Список пуст, но разрешена любая группа — качать есть что."""
        _globally(priorities=[], allow_any=True)

        assert resolve_for_series(SERIES)["ru"].allow_any is True

    def test_fallback_alone_is_enough(self) -> None:
        _globally(priorities=[], allow_any_fallback=True)

        assert "ru" in resolve_for_series(SERIES)


class TestHelpers:
    def test_scheduler_skips_work_while_everything_is_off(self) -> None:
        _globally(lang="ru", mode="off")

        assert any_language_enabled() is False

    def test_one_enabled_language_is_enough_to_work(self) -> None:
        _globally(lang="ru", mode="off")
        _globally(lang="ja", mode="configured")

        assert any_language_enabled() is True

    def test_configured_list_holds_titles_with_their_own_mode(self) -> None:
        _for_title(mode="on")

        assert configured_series_ids() == [SERIES]

    def test_a_title_with_only_its_own_list_counts_as_configured(self) -> None:
        _for_title(mode="default", priorities=["kazoku"])

        assert configured_series_ids() == [SERIES]

    def test_an_untouched_title_is_not_in_the_list(self) -> None:
        """Строка есть, но не решает ничего — показывать её незачем."""
        _for_title(mode="default", priorities=[])

        assert configured_series_ids() == []
