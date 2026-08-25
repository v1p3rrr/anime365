"""Под одним номером у тайтла живёт несколько разных серий — и это не редкость.

Замерено на базе пользователя: **48 таких пар в 42 тайтлах**, из них 35 с
локальными файлами. Раскладка по типам неожиданная:

| что с чем        | пар |
|------------------|-----|
| трейлер + серия  |  28 |
| фильм + трейлер  |  10 |
| ONA + серия      |   6 |
| остальное        |   4 |

То есть чаще всего виноват трейлер: у него **свой** `episodeInt`, и он
начинается с единицы. Реже — альтернативная нарезка: у тайтла 40024
двенадцатиминутные ONA-серии повторяют первые шесть телевизионных.

Пока переводы и длительности группировались по НОМЕРУ серии, всё это сливалось
в одно ведро. Пользователь увидел это так: в первой серии семь вариантов русских
субтитров вместо пяти и три «Crunchyroll» вместо двух, а у 12-минутной ONA
стояло «24 мин». В базе при этом лежали верные числа — 1433 с у телевизионной
серии против 869 с у ONA.

⚠️ Ловушка в этом файле уже была задокументирована на `url_by_episode`: трейлер
занимал ссылку первой серии, и кнопка «открыть серию» вела на страницу трейлера.
Вывод тогда не применили к соседним словарям — эти тесты закрывают и их.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete

from app.core.db import session_scope
from app.models.catalog import Episode, Series, Translation
from app.services.title_service import build_title_page

SERIES = 980_040

#: Идентификаторы взяты разными намеренно: весь смысл теста в том, что номер у
#: этих серий один, а личность разная.
TV_ONE = 371_590
ONA_ONE = 380_228
TRAILER = 379_525

#: Секунды. Настоящие медианы с сервера пользователя.
TV_DURATION = 1433.0
ONA_DURATION = 869.0
TRAILER_DURATION = 50.0


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    # Страница не ходит в сеть: переводы у неё из кэша, обновление — фоном.
    monkeypatch.setattr(
        "app.services.title_service.schedule_translation_refresh", lambda series_id: False
    )

    def wipe() -> None:
        with session_scope() as session:
            session.execute(delete(Translation).where(Translation.series_id == SERIES))
            session.execute(delete(Episode).where(Episode.series_id == SERIES))
            session.execute(delete(Series).where(Series.id == SERIES))

    wipe()
    yield
    wipe()


def _title() -> None:
    with session_scope() as session:
        session.merge(
            Series(
                id=SERIES,
                title="Same Number Twice",
                type="tv",
                number_of_episodes=6,
                # Иначе страница пойдёт в Anime365 за подробностями.
                has_details=True,
            )
        )


def _episode(episode_id: int, number: float, episode_type: str, full: str) -> None:
    with session_scope() as session:
        session.merge(
            Episode(
                id=episode_id,
                series_id=SERIES,
                episode_int=str(int(number)),
                episode_num=number,
                episode_type=episode_type,
                episode_full=full,
                is_active=True,
            )
        )


def _translation(tid: int, episode_id: int, authors: str, duration: float) -> None:
    with session_scope() as session:
        session.merge(
            Translation(
                id=tid,
                series_id=SERIES,
                episode_id=episode_id,
                type_kind="sub",
                type_lang="ru",
                authors_summary=authors,
                duration=duration,
                is_active=True,
            )
        )


def _page() -> dict:
    page = asyncio.run(build_title_page(SERIES))
    assert page is not None
    return page


def _row(page: dict, episode_id: int) -> dict:
    found = [e for e in page["episodes"] if e["id"] == episode_id]
    assert found, f"серия {episode_id} не попала на страницу"
    return found[0]


def _seed_tv_and_ona() -> None:
    """Раскладка тайтла 40024: телевизионная первая серия и ONA-нарезка."""
    _title()
    _episode(TV_ONE, 1, "tv", "1 серия")
    _episode(ONA_ONE, 1, "ona", "ONA 1 серия")

    # У телевизионной — пять вариантов, среди них два Crunchyroll: это дубль на
    # стороне сайта, и схлопывать его мы не собираемся.
    for tid, author in enumerate(
        ["AliveDub", "AniLiberty", "Crunchyroll", "Crunchyroll", "Ironclad"], start=1
    ):
        _translation(tid, TV_ONE, author, TV_DURATION)

    # У ONA — два, и именно они подмешивались в список выше.
    for tid, author in enumerate(["AniLiberty", "Crunchyroll"], start=100):
        _translation(tid, ONA_ONE, author, ONA_DURATION)


class TestTranslationsStayWithTheirEpisode:
    def test_the_tv_episode_shows_only_its_own(self):
        _seed_tv_and_ona()
        row = _row(_page(), TV_ONE)

        authors = sorted(t["authors"] for t in row["translations"])
        assert authors == ["AliveDub", "AniLiberty", "Crunchyroll", "Crunchyroll", "Ironclad"]

    def test_the_ona_episode_shows_only_its_own(self):
        _seed_tv_and_ona()
        row = _row(_page(), ONA_ONE)

        authors = sorted(t["authors"] for t in row["translations"])
        assert authors == ["AniLiberty", "Crunchyroll"]

    def test_the_count_the_user_saw(self):
        """Ровно тот симптом: семь вариантов вместо пяти, три Crunchyroll вместо двух."""
        _seed_tv_and_ona()
        row = _row(_page(), TV_ONE)

        assert len(row["translations"]) == 5, "в список серии попали чужие переводы"
        crunchyroll = [t for t in row["translations"] if t["authors"] == "Crunchyroll"]
        assert len(crunchyroll) == 2, "третий Crunchyroll приходит из ONA-серии"

    def test_a_trailer_does_not_leak_either(self):
        """Самый частый случай из замера — 28 пар из 48."""
        _title()
        _episode(TV_ONE, 1, "tv", "1 серия")
        _episode(TRAILER, 1, "preview", "Трейлер")
        _translation(1, TV_ONE, "Ironclad", TV_DURATION)
        _translation(2, TRAILER, "Реклама", TRAILER_DURATION)

        row = _row(_page(), TV_ONE)
        assert [t["authors"] for t in row["translations"]] == ["Ironclad"]


class TestDurationIsPerEpisode:
    def test_each_row_reports_its_own(self):
        _seed_tv_and_ona()
        page = _page()

        assert _row(page, TV_ONE)["duration"] == TV_DURATION
        assert _row(page, ONA_ONE)["duration"] == ONA_DURATION, (
            "у 12-минутной ONA показывалась медиана 24-минутной серии"
        )

    def test_the_trailer_does_not_drag_the_title_median(self):
        """Трейлер отсекается и при ключе по идентификатору — иначе он попадёт
        в `typical_duration`, которая считается по всем сериям сразу."""
        _title()
        _episode(TV_ONE, 1, "tv", "1 серия")
        _episode(TRAILER, 1, "preview", "Трейлер")
        _translation(1, TV_ONE, "Ironclad", TV_DURATION)
        _translation(2, TRAILER, "Реклама", TRAILER_DURATION)

        page = _page()
        assert page["typical_duration"] == TV_DURATION
        assert _row(page, TRAILER)["duration"] is None


class TestLocalStaysByNumber:
    def test_the_page_still_reports_the_episode_type(self):
        """Различить строки клиент обязан уметь: тип приходит в ответе.

        Локальные файлы по-прежнему ключуются номером — файл на диске назван
        `S01E01` и ни о какой ONA не знает. Значит скачанный перевод ONA-серии
        зажжёт «есть локально» у обеих строк, и единственное, чем они
        отличаются на странице, — это тип и название серии.
        """
        _seed_tv_and_ona()
        page = _page()

        assert _row(page, TV_ONE)["episode_type"] == "tv"
        assert _row(page, ONA_ONE)["episode_type"] == "ona"
        assert _row(page, ONA_ONE)["episode_full"] == "ONA 1 серия"
