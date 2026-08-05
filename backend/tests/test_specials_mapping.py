"""Папка спешлов уходит в своё вхождение, а не на сериал.

В Anime365 спешлы — самостоятельные записи. Проверено запросами к живому API:
у Toradora! это `Bentou no Gokui`, `Recap`, `SOS!`; у K-On! — `Keikaku!`,
`Live House!`, `Ura-On!!`, `Ura-On!`; у Yuru Yuri — `Doushite, Tomaranai`,
`Nachuyachumi!` и другие. Данные тайтлов ниже взяты из этих ответов.

Ловушка, ради которой файл и заведён: у папки спешлов **есть** номер сезона —
ноль. Без явной оговорки она выглядит обычным сезоном и забирает у сериала и
название, и идентификаторы, то есть садится прямо на него.
"""

from __future__ import annotations

from app.services.matcher import MatchContext, Matcher, SeriesCandidate, TitleIndex
from app.services.nfo_parser import NfoData


def series(
    sid: int,
    romaji: str,
    *,
    type_: str = "tv",
    year: int | None = None,
    episodes: int = 12,
    anilist: int | None = None,
    mal: int | None = None,
) -> SeriesCandidate:
    return SeriesCandidate(
        id=sid,
        titles={"romaji": romaji},
        all_titles=[romaji],
        type=type_,
        year=year,
        season=None,
        number_of_episodes=episodes,
        mal_id=mal,
        anilist_id=anilist,
    )


# Настоящая франшиза Toradora! из каталога.
TORADORA = TitleIndex(
    [
        series(4283, "toradora", year=2008, episodes=25, anilist=4224, mal=4224),
        series(9781, "Toradora!: Bentou no Gokui", type_="special", year=2011, episodes=1),
        series(9387, "Toradora! Recap", type_="ova", year=2009, episodes=1),
        series(9563, "Toradora!: SOS! Kuishinbou Banbanzai", type_="special", year=2009, episodes=4),
    ]
)

# Yuru Yuri: рядом со спешлом лежит `Mini Yuri` типа ona — полноценный
# веб-сериал, и путать его со спешлом нельзя.
YURU_YURI = TitleIndex(
    [
        series(260, "Yuru Yuri", year=2011, anilist=10495),
        series(9888, "Yuru Yuri: Doushite, Tomaranai", type_="special", year=2011, episodes=1),
        series(21401, "Mini Yuri", type_="ona", year=2019, episodes=4),
    ]
)


def _show_nfo(anilist: int, title: str) -> NfoData:
    data = NfoData(kind="tvshow", path="tvshow.nfo")
    data.title = title
    data.anilist_id = anilist
    return data


def _specials(index: TitleIndex, folder_name: str, nfo: NfoData, videos: int = 1):
    """Папка `Specials` внутри папки тайтла — так её видит сканер."""
    ctx = MatchContext(
        folder_name=folder_name,
        scope="series",
        nfo=nfo,
        own_name="Specials",
        is_child=True,
        own_season=0,
        video_count=videos,
    )
    return ctx, Matcher(index).match(ctx)


class TestSpecialsAreNotASeason:
    def test_context_knows_it_is_specials(self) -> None:
        ctx, _ = _specials(TORADORA, "Toradora!", _show_nfo(4224, "Toradora!"))

        assert ctx.is_specials is True
        assert ctx.inherits_parent is False, "иначе заберёт у сериала название и ID"

    def test_does_not_land_on_the_series(self) -> None:
        """Самая опасная ошибка: спешлы засчитались бы сериалу как его серии."""
        _, result = _specials(TORADORA, "Toradora!", _show_nfo(4224, "Toradora!"))

        assert result is None or result.series_id != 4283

    def test_lands_on_a_special_entry(self) -> None:
        _, result = _specials(TORADORA, "Toradora!", _show_nfo(4224, "Toradora!"))

        assert result is not None
        assert result.series_id in {9781, 9387, 9563}

    def test_web_series_is_not_mistaken_for_a_special(self) -> None:
        """`Mini Yuri` — тип `ona`, четыре серии. Это не спешл."""
        _, result = _specials(YURU_YURI, "Yuru Yuri", _show_nfo(10495, "Yuru Yuri"))

        assert result is None or result.series_id != 21401
        assert result is None or result.series_id != 260


class TestOrdinarySeasonStillWorks:
    """Страховка: правило про спешлы не должно задеть обычный сезон."""

    def test_season_one_still_takes_the_parent_id(self) -> None:
        ctx = MatchContext(
            folder_name="Toradora!",
            scope="series",
            nfo=_show_nfo(4224, "Toradora!"),
            own_name="Season 1",
            is_child=True,
            own_season=1,
            video_count=25,
        )

        assert ctx.is_specials is False
        assert ctx.inherits_parent is True

        result = Matcher(TORADORA).match(ctx)
        assert result is not None and result.series_id == 4283

    def test_title_folder_is_unaffected(self) -> None:
        ctx = MatchContext(
            folder_name="Toradora!",
            scope="series",
            nfo=_show_nfo(4224, "Toradora!"),
            own_name="Toradora!",
            is_child=False,
            video_count=25,
        )

        assert ctx.is_specials is False
        result = Matcher(TORADORA).match(ctx)
        assert result is not None and result.series_id == 4283
