"""Сезонная папка не должна садиться на спиноф или на первый сезон.

Все случаи — с живой библиотеки после прогона 2026-08-05:

| Папка | Куда уехало |
|---|---|
| `Bang Dream!/Season 2` (13 видео) | `BanG Dream! Garupa☆Pico: Oomori` |
| `Frieren …/Season 2` (10 видео) | `Sousou no Frieren: ●● no Mahou` (чиби) |
| `Oshi no Ko/Season 3` (11 видео) | `[Oshi no Ko]`, первый сезон |

**Число серий здесь бессильно принципиально**, и это возражение пользователя:
скачано может быть десять из двенадцати, а у чиби-сериала рядом их двадцать
четыре — или наоборот, батч из двенадцати серий чиби. Считать нечего.

Работает другой признак: у верного ответа номер сезона написан прямо в
заголовке («2nd Season»), а у спинофа и у первого сезона — нет.
"""

from __future__ import annotations

from app.services.matcher import MatchContext, Matcher, SeriesCandidate, TitleIndex, season_in_title
from app.services.nfo_parser import NfoData


def series(
    sid: int,
    romaji: str,
    *,
    type_: str = "tv",
    year: int | None = None,
    season: str | None = None,
    episodes: int = 12,
    anilist: int | None = None,
) -> SeriesCandidate:
    return SeriesCandidate(
        id=sid,
        titles={"romaji": romaji},
        all_titles=[romaji],
        type=type_,
        year=year,
        season=season,
        number_of_episodes=episodes,
        mal_id=None,
        anilist_id=anilist,
    )


class TestSeasonInTitle:
    def test_reads_the_forms_that_occur(self) -> None:
        assert season_in_title("BanG Dream! 2nd Season") == 2
        assert season_in_title("[Oshi no Ko] 3rd Season") == 3
        assert season_in_title("Something Season 4") == 4
        assert season_in_title("Youjo Senki II") == 2

    def test_does_not_invent_seasons(self) -> None:
        assert season_in_title("BanG Dream! Garupa Pico: Oomori") is None
        assert season_in_title("Sousou no Frieren") is None
        # Часть фильма — не сезон, и голая цифра в конце номером не считается.
        assert season_in_title("Girls Band Cry Movie 2") is None
        assert season_in_title("Yuru Yuri San Hai!") is None


class TestBangDream:
    """Чиби-спиноф вышел в тот же сезон года, что и настоящий второй."""

    INDEX = TitleIndex(
        [
            series(1, "BanG Dream!", year=2017, season="Зима 2017", episodes=13),
            series(2, "BanG Dream! 2nd Season", year=2019, season="Зима 2019", episodes=13),
            series(
                3,
                "BanG Dream! Garupa Pico: Oomori",
                type_="ona",
                year=2019,
                season="Зима 2019",
                episodes=26,
            ),
        ]
    )

    def _ctx(self, season: int, videos: int = 13) -> MatchContext:
        show = NfoData(kind="tvshow", path="tvshow.nfo")
        show.title = "BanG Dream!"
        season_nfo = NfoData(kind="season", path="season.nfo")
        season_nfo.season = season
        season_nfo.premiered = "2019-01-03"
        return MatchContext(
            folder_name="BanG Dream!",
            scope="series",
            nfo=show,
            season_nfo=season_nfo,
            own_name=f"Season {season}",
            is_child=True,
            video_count=videos,
        )

    def test_second_season_does_not_land_on_the_spinoff(self) -> None:
        result = Matcher(self.INDEX).match(self._ctx(2))

        assert result is not None
        assert result.series_id != 3, "уехало на чиби-спиноф"
        assert result.series_id == 2

    def test_partial_download_changes_nothing(self) -> None:
        """Скачано три серии из тринадцати — признак не про количество."""
        result = Matcher(self.INDEX).match(self._ctx(2, videos=3))

        assert result is not None and result.series_id == 2


class TestOshiNoKo:
    """Третий сезон уезжал на первый: у первого номера в названии нет."""

    INDEX = TitleIndex(
        [
            series(10, "[Oshi no Ko]", year=2023, season="Весна 2023", episodes=11),
            series(11, "[Oshi no Ko] 2nd Season", year=2024, season="Лето 2024", episodes=13),
            series(12, "[Oshi no Ko] 3rd Season", year=2026, season="Зима 2026", episodes=12),
        ]
    )

    def test_third_season_does_not_land_on_the_first(self) -> None:
        show = NfoData(kind="tvshow", path="tvshow.nfo")
        show.title = "[Oshi no Ko]"
        season_nfo = NfoData(kind="season", path="season.nfo")
        season_nfo.season = 3
        season_nfo.premiered = "2026-01-10"
        ctx = MatchContext(
            folder_name="[Oshi no Ko]",
            scope="series",
            nfo=show,
            season_nfo=season_nfo,
            own_name="Season 3",
            is_child=True,
            video_count=11,
        )

        result = Matcher(self.INDEX).match(ctx)

        assert result is not None
        assert result.series_id == 12


class TestFirstSeasonIsUnaffected:
    """Страховка: у первого сезона номера в названии нет, и это нормально."""

    INDEX = TitleIndex(
        [
            series(20, "Sousou no Frieren", year=2023, season="Осень 2023", episodes=28),
            series(
                21,
                "Sousou no Frieren: Mahou",
                type_="ona",
                year=2023,
                season="Осень 2023",
                episodes=12,
            ),
        ]
    )

    def test_base_title_still_matches(self) -> None:
        show = NfoData(kind="tvshow", path="tvshow.nfo")
        show.title = "Sousou no Frieren"
        season_nfo = NfoData(kind="season", path="season.nfo")
        season_nfo.season = 1
        season_nfo.premiered = "2023-09-29"
        ctx = MatchContext(
            folder_name="Sousou no Frieren",
            scope="series",
            nfo=show,
            season_nfo=season_nfo,
            own_name="Season 1",
            is_child=True,
            video_count=28,
        )

        result = Matcher(self.INDEX).match(ctx)

        assert result is not None and result.series_id == 20


class TestBracketedTitleSurvives:
    """`[Oshi no Ko]` терялся целиком, и к сезонам это отношения не имело.

    Скобки вырезаются ради релизного тега (`[Ohys-Raws] Yuru Camp` — это
    «Yuru Camp»). Но у этого тайтла в скобках **всё название**, и после чистки
    не оставалось ничего: искать было нечем ни точным совпадением, ни префиксом.
    """

    def test_whole_name_in_brackets_is_kept(self) -> None:
        from app.services.filename_rules import normalize_for_match

        assert normalize_for_match("[Oshi no Ko]")[0] == "oshi no ko"
        assert normalize_for_match("[Oshi no Ko] 3rd Season") == ("oshi no ko", 3)

    def test_release_tag_is_still_stripped(self) -> None:
        """Обратная страховка: тег релиз-группы по-прежнему выбрасывается."""
        from app.services.filename_rules import normalize_for_match

        assert normalize_for_match("[Ohys-Raws] Yuru Camp Season 2") == ("yuru camp", 2)


class TestEmphasisMarks:
    """«K-On!» и «K-On!!» после нормализации неразличимы — различает хвост.

    ⚠️ Правило «`!!` значит второй сезон» здесь намеренно **не** сделано:
    `Haikyuu!!` — первый сезон, у которого две восклицательные в самом
    названии. Считается не толкование, а совпадение числа знаков.
    """

    INDEX = TitleIndex(
        [
            series(30, "K-On!", year=2009, episodes=13),
            series(31, "K-On!!", year=2010, episodes=26),
            series(32, "Haikyuu!!", year=2014, episodes=25),
        ]
    )

    def _match(self, folder_name: str):
        from app.services.nfo_parser import NfoData

        nfo = NfoData(kind="tvshow", path="tvshow.nfo")
        nfo.title = folder_name
        ctx = MatchContext(
            folder_name=folder_name,
            scope="series",
            nfo=nfo,
            own_name=folder_name,
            is_child=False,
            video_count=13,
        )
        return Matcher(self.INDEX).match(ctx)

    def test_counts_the_tail(self) -> None:
        from app.services.matcher import emphasis

        assert emphasis("K-On!") == 1
        assert emphasis("K-On!!") == 2
        assert emphasis("K-ON！！") == 2, "японская полноширинная форма — та же роль"
        assert emphasis("Yuru Yuri♪♪") == 2
        assert emphasis("Toradora") == 0

    def test_single_mark_picks_the_first_season(self) -> None:
        result = self._match("K-On!")

        assert result is not None and result.series_id == 30

    def test_double_mark_picks_the_sequel(self) -> None:
        result = self._match("K-On!!")

        assert result is not None and result.series_id == 31

    def test_title_that_simply_ends_with_marks(self) -> None:
        """`Haikyuu!!` не должен превратиться во «второй сезон» чего-либо."""
        result = self._match("Haikyuu!!")

        assert result is not None and result.series_id == 32
