"""Тесты матчера.

Каждый случай здесь — ошибка, найденная прогоном по реальной библиотеке.
Синтетика тут только в том, что каталог маленький; сами ситуации настоящие.
"""

from __future__ import annotations

import pytest

from app.services.matcher import (
    MatchContext,
    Matcher,
    MatchSource,
    SeriesCandidate,
    TitleIndex,
    season_label_from_premiered,
)
from app.services.nfo_parser import NfoData


def series(
    sid: int,
    romaji: str,
    *,
    type_: str = "tv",
    year: int | None = None,
    season: str | None = None,
    episodes: int = 12,
    mal: int | None = None,
    anilist: int | None = None,
    extra_titles: list[str] | None = None,
    japanese: str | None = None,
) -> SeriesCandidate:
    titles = {"romaji": romaji}
    if japanese:
        titles["ja"] = japanese
    return SeriesCandidate(
        id=sid,
        titles=titles,
        # Тайтл почти всегда содержит своё же название и в allTitles — именно это
        # раньше ломало проверку «ровно один кандидат».
        all_titles=[romaji, *(extra_titles or [])],
        type=type_,
        year=year,
        season=season,
        number_of_episodes=episodes,
        mal_id=mal,
        anilist_id=anilist,
    )


def nfo(kind: str = "tvshow", **kw) -> NfoData:
    data = NfoData(kind=kind, path="test.nfo")
    for key, value in kw.items():
        setattr(data, key, value)
    return data


class TestSeasonLabel:
    def test_quarters(self) -> None:
        assert season_label_from_premiered(2012, 7) == "Лето 2012"
        assert season_label_from_premiered(2015, 10) == "Осень 2015"
        assert season_label_from_premiered(2011, 11) == "Осень 2011"

    def test_december_belongs_to_next_winter(self) -> None:
        # Каталог относит декабрьские премьеры к зиме следующего года.
        assert season_label_from_premiered(2020, 12) == "Зима 2021"


class TestDeduplication:
    """Один тайтл не должен попадать в список кандидатов несколько раз."""

    def test_same_title_in_titles_and_all_titles(self) -> None:
        index = TitleIndex([series(1, "Zombieland Saga", extra_titles=["Zombieland Saga"] * 3)])
        assert index.by_norm_title["zombieland saga"] == [1]

    def test_exact_match_wins_over_fuzzy(self) -> None:
        # До исправления дубликаты ломали проверку «ровно один кандидат»,
        # и точное совпадение всегда проваливалось в нечёткий поиск.
        index = TitleIndex(
            [
                series(19323, "Zombieland Saga", extra_titles=["Zombie Land Saga"] * 3),
                series(19343, "Force of Will the Movie", type_="movie"),
            ]
        )
        matcher = Matcher(index)
        result = matcher.match(
            MatchContext(folder_name="Zombie Land Saga", scope="series", nfo=nfo(title="Zombie Land Saga"))
        )
        assert result is not None
        assert result.series_id == 19323
        assert result.source is MatchSource.EXACT_TITLE


class TestScorer:
    def test_short_key_does_not_beat_full_title(self) -> None:
        # token_set_ratio давал «zombie» те же 100 баллов, что и полному названию,
        # поэтому короткие мусорные записи выигрывали.
        index = TitleIndex(
            [
                series(1, "Zombieland Saga Revenge", year=2021),
                series(2, "Zombie", type_="movie"),
            ]
        )
        best = index.fuzzy("zombieland saga revenge", limit=5)
        assert best[0][0] == "zombieland saga revenge"
        short = next((score for key, score in best if key == "zombie"), 0)
        assert short < 90


class TestServiceTypes:
    def test_commercial_never_wins(self) -> None:
        index = TitleIndex(
            [
                series(19321, "Violet Evergarden Movie", type_="movie", year=2020, episodes=1),
                series(28109, "Violet Evergarden CMs", type_="cm", year=2020, episodes=1),
            ]
        )
        matcher = Matcher(index)
        result = matcher.match(
            MatchContext(
                folder_name="Violet Evergarden - The Movie",
                scope="movie",
                nfo=nfo("movie", title="Violet Evergarden Movie", year=2020),
                local_episode_count=1,
            )
        )
        assert result is not None
        assert index.by_id[result.series_id].type != "cm"


class TestIdCascade:
    def test_anilist_id_is_trusted(self) -> None:
        index = TitleIndex([series(260, "Yuru Yuri", anilist=10495, year=2011)])
        matcher = Matcher(index)
        result = matcher.match(
            MatchContext(
                folder_name="YuruYuri - Happy Go Lily",
                scope="series",
                nfo=nfo(title="Yuru Yuri", anilist_id=10495),
            )
        )
        assert result is not None
        assert result.source is MatchSource.ANILIST_ID
        assert result.confidence == 100.0

    def test_wrong_shikimori_id_is_rejected(self) -> None:
        # У YuruYuri <shikimoriid> указывает на «Pu-Neko» — принимать такое нельзя.
        index = TitleIndex(
            [
                series(260, "Yuru Yuri", mal=10495, year=2011),
                series(999, "Puu-Neko Shougekijou", mal=11589, year=2011),
            ]
        )
        matcher = Matcher(index)
        result = matcher.match(
            MatchContext(
                folder_name="YuruYuri - Happy Go Lily",
                scope="series",
                nfo=nfo(title="Yuru Yuri", shikimori_id=11589),
            )
        )
        # По названию совпадения с Pu-Neko нет, поэтому ID отвергается,
        # и тайтл находится строковым каскадом.
        assert result is not None
        assert result.series_id == 260


class TestSeasonCascade:
    """Сезоны разводятся по дате премьеры: одного года не хватает."""

    @pytest.fixture
    def yuru_yuri_index(self) -> TitleIndex:
        return TitleIndex(
            [
                series(260, "Yuru Yuri", year=2011, season="Лето 2011", anilist=10495),
                series(3499, "Yuru Yuri♪♪", year=2012, season="Лето 2012"),
                series(13077, "Yuru Yuri San☆Hai!", year=2015, season="Осень 2015"),
                series(8613, "Yuru Yuri Nachuyachumi!", type_="ova", year=2015, season="Зима 2015", episodes=1),
                series(12880, "Yuru Yuri Nachuyachumi!+", type_="tv_special", year=2015, season="Лето 2015", episodes=2),
                series(9888, "Yuru Yuri: Paradox Eternal", type_="special", year=2011, season="Осень 2011", episodes=1),
            ]
        )

    @pytest.mark.parametrize(
        ("premiered", "season_no", "expected"),
        [
            ("2012-07-02", 2, 3499),
            ("2015-10-05", 3, 13077),
            ("2011-11-16", 0, 9888),
        ],
    )
    def test_premiered_resolves_season(
        self, yuru_yuri_index: TitleIndex, premiered: str, season_no: int, expected: int
    ) -> None:
        matcher = Matcher(yuru_yuri_index)
        result = matcher.match(
            MatchContext(
                folder_name="YuruYuri - Happy Go Lily",
                scope="series",
                nfo=nfo(title="Yuru Yuri", anilist_id=10495),
                season_nfo=nfo("season", premiered=premiered, season=season_no),
                local_episode_count=12,
            )
        )
        assert result is not None, f"сезон {season_no} не смаппился"
        assert result.series_id == expected

    def test_year_alone_is_ambiguous(self, yuru_yuri_index: TitleIndex) -> None:
        # В 2015-м у YuruYuri три разных тайтла — именно поэтому нужен квартал.
        same_year = [c for c in yuru_yuri_index.by_id.values() if c.year == 2015]
        assert len(same_year) == 3

    def test_first_season_still_uses_id(self, yuru_yuri_index: TitleIndex) -> None:
        matcher = Matcher(yuru_yuri_index)
        result = matcher.match(
            MatchContext(
                folder_name="YuruYuri - Happy Go Lily",
                scope="series",
                nfo=nfo(title="Yuru Yuri", anilist_id=10495),
                season_nfo=nfo("season", premiered="2011-07-05", season=1),
                local_episode_count=12,
            )
        )
        assert result is not None
        assert result.series_id == 260

    def test_sequel_is_separate_series(self) -> None:
        # Локально это «Zombie Land Saga/Season 2», а в каталоге — отдельный тайтл.
        index = TitleIndex(
            [
                series(19323, "Zombieland Saga", year=2018, season="Осень 2018", anilist=101633),
                series(21253, "Zombieland Saga Revenge", year=2021, season="Весна 2021"),
            ]
        )
        matcher = Matcher(index)
        result = matcher.match(
            MatchContext(
                folder_name="Zombie Land Saga",
                scope="series",
                nfo=nfo(title="Zombieland Saga", anilist_id=101633),
                season_nfo=nfo("season", premiered="2021-04-08", season=2),
                local_episode_count=12,
            )
        )
        assert result is not None
        assert result.series_id == 21253


class TestNoMatch:
    def test_unknown_folder_returns_none(self) -> None:
        index = TitleIndex([series(1, "Something Completely Different")])
        matcher = Matcher(index)
        result = matcher.match(
            MatchContext(folder_name="Zzzz Unmatchable Qqqq", scope="series")
        )
        assert result is None
class TestGuessedSeasonDate:
    """Отбор при неоднозначности берёт дату там же, где и сверка.

    ⚠️ Ожидание здесь не «правильный сезон», а «одинаковый источник даты».
    Расхождение было настоящим: `_expected_year` сознательно отдаёт `None`,
    когда номер сезона выведен по именам файлов (D-91), а отбор читал год и
    квартал напрямую из `tvshow.nfo` — то есть у первого сезона. Починить это
    ниже по цепочке было нечем: `_disambiguate` возвращает **одного**
    кандидата, и верный до сверки не доезжал вовсе.
    """

    def _index(self) -> TitleIndex:
        # Оба сезона названы одинаково — по строке они неразличимы, и решает
        # именно отбор.
        return TitleIndex(
            [
                series(1, "Classroom of the Elite", year=2017, season="Лето 2017", episodes=12),
                series(2, "Classroom of the Elite", year=2025, season="Лето 2025", episodes=13),
            ]
        )

    def _ctx(self) -> MatchContext:
        # Ни `season.nfo`, ни «Season 4» в имени: номер взят из `S04E01…`.
        # А `tvshow.nfo` описывает первый сезон — его дата к четвёртому
        # отношения не имеет.
        parent = nfo(title="Classroom of the Elite", year=2017, premiered="2017-07-12")
        return MatchContext(
            folder_name="Classroom of the Elite",
            scope="series",
            nfo=parent,
            own_nfo=parent,
            own_name="Classroom of the Elite",
            video_count=13,
            local_episode_count=13,
            season_from_files=4,
        )

    def test_quarter_of_first_season_does_not_narrow(self) -> None:
        matcher = Matcher(self._index())
        ctx = self._ctx()
        assert matcher._expected_quarter(ctx) is None
        assert matcher._expected_year(ctx) is None

    def test_fourth_season_is_not_replaced_by_the_first(self) -> None:
        matcher = Matcher(self._index())
        result = matcher.match(self._ctx())
        assert result is not None
        assert result.series_id == 2

    def test_own_season_date_is_still_trusted(self) -> None:
        # Обратная сторона: когда дата **своя**, отбор обязан ею пользоваться.
        matcher = Matcher(self._index())
        ctx = self._ctx()
        ctx.season_nfo = nfo("season", season=1, premiered="2017-07-12")
        assert matcher._expected_quarter(ctx) == (2017, 7)
        result = matcher.match(ctx)
        assert result is not None
        assert result.series_id == 1

