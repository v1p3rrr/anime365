"""Смена правила нормализации не должна ломать уже настроенное.

Правило изменилось: скобки выбрасываются целиком
([D-172](../../claude/09-decisions.md)). Само по себе это правильно, но у
изменения два хвоста, и оба тихие.

**Хвост первый — каталог групп.** Он наполняется лениво и живёт долго, поэтому
старые ключи остаются в нём навсегда. У пользователя в подсказке было шесть
отдельных `YakuSub Studio`: с `(Aero)`, с тремя разными тройками людей и без
скобок вовсе. Замерено: 79 записей из 498 со скобками в ключе.

**Хвост второй, опаснее.** Список приоритетов хранит **нормализованный ключ** —
его кладёт туда `AuthorPriorityList.tsx`, — а обычная запись сверяется как
`entry == normalized_key`. Стоит правилу измениться, и сохранённый
`yakusub studio (aero)` перестаёт совпадать с новым ключом `yakusub studio`.
Внешне не происходит ничего: субтитры просто не качаются.

Отдельно проверяется требование пользователя: одна запись `yakusub studio`
обязана подхватывать **любое** написание — и голое имя, и `(Aero)`, — а кто из
них поедет, решает приоритет и наличие.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.autodownload import AutoDownloadRule, SubtitleAuthor
from app.services.autodownload import rank_candidates
from app.services.autodownload_rules import Resolved
from app.services.subtitle_authors import (
    normalize_author,
    recompute_keys,
    renormalize_saved_entries,
)

LANG = "zz"  # выдуманный язык: не мешаем настоящим данным


@pytest.fixture(autouse=True)
def clean():
    def wipe() -> None:
        with session_scope() as session:
            session.execute(delete(SubtitleAuthor).where(SubtitleAuthor.lang == LANG))
            session.execute(delete(AutoDownloadRule).where(AutoDownloadRule.lang == LANG))

    wipe()
    yield
    wipe()


def _catalog(name: str, seen: int, days_ago: int = 0) -> None:
    """Запись каталога со СТАРЫМ ключом — как она лежит в базе пользователя."""
    with session_scope() as session:
        session.add(
            SubtitleAuthor(
                lang=LANG,
                key=name.casefold(),  # прежнее правило скобки оставляло
                name=name,
                seen=seen,
                last_seen=datetime.utcnow() - timedelta(days=days_ago),
            )
        )


def _rows() -> list[SubtitleAuthor]:
    with session_scope() as session:
        return list(
            session.execute(
                select(SubtitleAuthor).where(SubtitleAuthor.lang == LANG)
            ).scalars()
        )


def _candidate(tid: int, authors: str, priority: int) -> dict:
    return {
        "id": tid,
        "authors": authors,
        "key": normalize_author(authors),
        "priority": priority,
    }


class TestOneEntryTakesEveryVariant:
    """Требование пользователя: `YakuSub Studio` качает и голое, и `(Aero)`."""

    def test_both_variants_match_a_single_entry(self):
        resolved = Resolved(lang=LANG, priorities=["yakusub studio"])
        candidates = [
            _candidate(1, "YakuSub Studio", priority=1),
            _candidate(2, "YakuSub Studio (Aero)", priority=9),
        ]

        ranked = rank_candidates(candidates, resolved)
        assert len(ranked) == 2, "одно из написаний выпало из выборки"

    def test_the_better_rated_one_wins(self):
        """Кто поедет — решает оценка Anime365, а не форма записи имени."""
        resolved = Resolved(lang=LANG, priorities=["yakusub studio"])
        candidates = [
            _candidate(1, "YakuSub Studio", priority=1),
            _candidate(2, "YakuSub Studio (Aero)", priority=9),
        ]

        assert rank_candidates(candidates, resolved)[0]["id"] == 2

    def test_only_the_bracketed_one_is_still_taken(self):
        """Если голого написания нет вовсе, скобочное обязано подойти."""
        resolved = Resolved(lang=LANG, priorities=["yakusub studio"])
        candidates = [_candidate(2, "YakuSub Studio (Helge & Selfish)", priority=3)]

        assert [item["id"] for item in rank_candidates(candidates, resolved)] == [2]

    def test_a_different_studio_still_does_not_match(self):
        resolved = Resolved(lang=LANG, priorities=["yakusub studio"])
        candidates = [_candidate(3, "MedusaSub (Lilium)", priority=9)]

        assert rank_candidates(candidates, resolved) == []


class TestCatalogIsRekeyed:
    def test_variants_merge_into_one_row(self):
        _catalog("YakuSub Studio (Aero)", seen=27)
        _catalog("YakuSub Studio", seen=22)
        _catalog("YakuSub Studio (Harushechka & Helge & Lulu)", seen=9)

        merged = recompute_keys()

        assert merged == 2
        rows = _rows()
        assert len(rows) == 1
        assert rows[0].key == "yakusub studio"

    def test_counters_are_summed(self):
        _catalog("YakuSub Studio (Aero)", seen=27)
        _catalog("YakuSub Studio", seen=22)

        recompute_keys()

        assert _rows()[0].seen == 49

    def test_the_shown_name_loses_its_brackets(self):
        """Иначе в подсказке останется `YakuSub Studio (Aero)` как имя группы."""
        _catalog("YakuSub Studio (Aero)", seen=27)
        _catalog("YakuSub Studio", seen=22)

        recompute_keys()

        assert _rows()[0].name == "YakuSub Studio"

    def test_the_latest_meeting_is_kept(self):
        _catalog("YakuSub Studio (Aero)", seen=27, days_ago=30)
        _catalog("YakuSub Studio", seen=22, days_ago=1)

        recompute_keys()

        assert (datetime.utcnow() - _rows()[0].last_seen) < timedelta(days=2)

    def test_different_studios_are_left_apart(self):
        _catalog("MedusaSub (Lilium)", seen=5)
        _catalog("SoundSub (Lilium)", seen=5)

        recompute_keys()

        assert sorted(row.key for row in _rows()) == ["medusasub", "soundsub"]

    def test_running_twice_changes_nothing(self):
        """Вызывается при каждом старте — обязана быть идемпотентной."""
        _catalog("YakuSub Studio (Aero)", seen=27)
        _catalog("YakuSub Studio", seen=22)

        recompute_keys()
        before = [(row.key, row.name, row.seen) for row in _rows()]

        assert recompute_keys() == 0
        assert [(row.key, row.name, row.seen) for row in _rows()] == before


class TestSavedListsSurvive:
    def _rule(self, priorities: list[str], blacklist: list[str] | None = None) -> None:
        with session_scope() as session:
            session.merge(
                AutoDownloadRule(
                    lang=LANG, priorities=priorities, blacklist=blacklist or []
                )
            )

    def _stored(self) -> tuple[list[str], list[str]]:
        with session_scope() as session:
            row = session.get(AutoDownloadRule, LANG)
            return list(row.priorities or []), list(row.blacklist or [])

    def test_an_old_key_is_brought_up_to_date(self):
        """Тот самый тихий слом: запись со скобками перестала бы совпадать."""
        self._rule(["yakusub studio (aero)"])

        renormalize_saved_entries()

        assert self._stored()[0] == ["yakusub studio"]

    def test_the_order_is_the_priority_and_survives(self):
        self._rule(["crunchyroll", "yakusub studio (aero)", "wakanim"])

        renormalize_saved_entries()

        assert self._stored()[0] == ["crunchyroll", "yakusub studio", "wakanim"]

    def test_collapsed_duplicates_do_not_repeat(self):
        self._rule(["yakusub studio (aero)", "yakusub studio"])

        renormalize_saved_entries()

        assert self._stored()[0] == ["yakusub studio"]

    def test_the_blacklist_is_treated_the_same(self):
        self._rule([], ["medusasub (lilium)"])

        renormalize_saved_entries()

        assert self._stored()[1] == ["medusasub"]

    def test_a_regular_expression_is_left_alone(self):
        """Шаблон пишет человек, нормализовать его бессмысленно и вредно."""
        self._rule(["re:yaku.*(aero)"])

        renormalize_saved_entries()

        assert self._stored()[0] == ["re:yaku.*(aero)"]

    def test_running_twice_changes_nothing(self):
        self._rule(["yakusub studio (aero)", "crunchyroll"])

        renormalize_saved_entries()
        before = self._stored()

        assert renormalize_saved_entries() == 0
        assert self._stored() == before
