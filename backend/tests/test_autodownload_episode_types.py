"""Автоскачивание не должно брать субтитры от чужой серии под тем же номером.

Найдено при проверке соседнего исправления ([D-170](../../claude/09-decisions.md)).
Страницу тайтла развели по идентификатору серии, а здесь ключом остался **номер**
— и остаться он обязан: субтитры подбираются к файлу на диске, а файл назван
`S01E01` и о личности серии на сайте не знает.

Отсюда и опасность. Под одним номером у тайтла живёт несколько разных серий —
замерено на базе пользователя: 48 пар в 42 тайтлах, чаще всего трейлер рядом с
первой серией (28 случаев), реже альтернативная нарезка (ONA + серия, 6). Все их
переводы попадали в одно ведро, дальше побеждал тот, у кого выше `priority`.

То есть к 24-минутной серии могли молча приехать субтитры от 12-минутного
ONA-варианта — с чужими таймингами и без второй половины, — или вовсе от
трейлера. Молча: файл лёг рядом с видео, в очереди успех, разбираться пришлось бы
глазами.

Теперь ведро сужается до выбора: не-содержимое отбрасывается всегда, а при
нескольких видах содержимого под одним номером остаются те, чей вид совпадает с
видом самого тайтла.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.core.db import session_scope
from app.models.catalog import Episode, Series, Translation
from app.services.autodownload import _candidates

SERIES = 980_041

TV_ONE = 371_590
ONA_ONE = 380_228
TRAILER = 379_525


@pytest.fixture(autouse=True)
def clean():
    def wipe() -> None:
        with session_scope() as session:
            session.execute(delete(Translation).where(Translation.series_id == SERIES))
            session.execute(delete(Episode).where(Episode.series_id == SERIES))
            session.execute(delete(Series).where(Series.id == SERIES))

    wipe()
    yield
    wipe()


def _title(series_type: str) -> None:
    with session_scope() as session:
        session.merge(
            Series(id=SERIES, title="Same Number Twice", type=series_type, has_details=True)
        )


def _episode(episode_id: int, number: float, episode_type: str) -> None:
    with session_scope() as session:
        session.merge(
            Episode(
                id=episode_id,
                series_id=SERIES,
                episode_int=str(int(number)),
                episode_num=number,
                episode_type=episode_type,
                is_active=True,
            )
        )


def _translation(tid: int, episode_id: int, authors: str, priority: int) -> None:
    with session_scope() as session:
        session.merge(
            Translation(
                id=tid,
                series_id=SERIES,
                episode_id=episode_id,
                type_kind="sub",
                type_lang="ru",
                authors_summary=authors,
                priority=priority,
                is_active=True,
            )
        )


def _authors_for(number: float) -> list[str]:
    return sorted(item["authors"] for item in _candidates(SERIES, "ru").get(number, []))


class TestTrailersAreNeverCandidates:
    def test_a_trailer_under_the_first_episode_number(self):
        """Самый частый случай замера — 28 пар из 48."""
        _title("tv")
        _episode(TV_ONE, 1, "tv")
        _episode(TRAILER, 1, "preview")
        _translation(1, TV_ONE, "Ironclad", priority=1)
        # У трейлера приоритет ВЫШЕ: без фильтра он бы и победил.
        _translation(2, TRAILER, "Реклама", priority=99)

        assert _authors_for(1) == ["Ironclad"]

    def test_a_title_of_only_trailers_yields_nothing(self):
        _title("tv")
        _episode(TRAILER, 1, "preview")
        _translation(1, TRAILER, "Реклама", priority=99)

        assert _authors_for(1) == []


class TestAlternateCutsLoseToTheCanonicalOnes:
    def test_a_tv_title_prefers_its_tv_episodes(self):
        """Раскладка тайтла 40024: ONA-нарезка под номерами телевизионных серий."""
        _title("tv")
        _episode(TV_ONE, 1, "tv")
        _episode(ONA_ONE, 1, "ona")
        _translation(1, TV_ONE, "Ironclad", priority=1)
        # У ONA приоритет выше — и раньше выигрывала именно она.
        _translation(2, ONA_ONE, "Crunchyroll", priority=50)

        assert _authors_for(1) == ["Ironclad"]

    def test_an_ona_title_prefers_its_ona_episodes(self):
        """Правило симметрично: канонично то, что совпадает с видом тайтла."""
        _title("ona")
        _episode(TV_ONE, 1, "tv")
        _episode(ONA_ONE, 1, "ona")
        _translation(1, TV_ONE, "Ironclad", priority=99)
        _translation(2, ONA_ONE, "Crunchyroll", priority=1)

        assert _authors_for(1) == ["Crunchyroll"]

    def test_without_a_match_nothing_is_thrown_away(self):
        """Остаться без кандидатов хуже, чем выбрать из того, что есть.

        У тайтла вида `special` под номером лежат `tv` и `ona` — совпадения нет
        ни с одним. Отбрасывать в таком случае нечего и незачем.
        """
        _title("special")
        _episode(TV_ONE, 1, "tv")
        _episode(ONA_ONE, 1, "ona")
        _translation(1, TV_ONE, "Ironclad", priority=1)
        _translation(2, ONA_ONE, "Crunchyroll", priority=50)

        assert _authors_for(1) == ["Crunchyroll", "Ironclad"]


class TestOrdinaryTitlesAreUntouched:
    def test_a_single_kind_keeps_every_candidate(self):
        """Обычный тайтл без совпадений номеров ведёт себя как раньше."""
        _title("tv")
        _episode(TV_ONE, 1, "tv")
        _translation(1, TV_ONE, "Ironclad", priority=1)
        _translation(2, TV_ONE, "AniLiberty", priority=2)

        assert _authors_for(1) == ["AniLiberty", "Ironclad"]

    def test_the_helper_key_does_not_leak_to_callers(self):
        """Вид серии — служебное поле отбора, наружу ему незачем."""
        _title("tv")
        _episode(TV_ONE, 1, "tv")
        _translation(1, TV_ONE, "Ironclad", priority=1)

        for item in _candidates(SERIES, "ru")[1.0]:
            assert "_episode_type" not in item
