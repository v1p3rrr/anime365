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
    extra: list[str] | None = None,
) -> SeriesCandidate:
    return SeriesCandidate(
        id=sid,
        titles={"romaji": romaji},
        all_titles=[romaji, *(extra or [])],
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
        series(
            9781,
            "Toradora!: Bentou no Gokui",
            type_="special",
            year=2011,
            episodes=1,
            # Варианты названий настоящие — из ответа /api/series/9781.
            extra=[
                "Toradora! Special",
                "Toradora!: Bento Battle",
                "Toradora!: The True Meaning of Bento",
            ],
        ),
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
        """Самая опасная ошибка: спешлы засчитались бы сериалу как его серии.

        ⚠️ Утверждение положительное намеренно. Прежнее «`None` или не сериал»
        проходило бы при **полностью сломанном** матчере, возвращающем `None`
        на всё, — то есть не проверяло ничего.
        """
        _, result = _specials(TORADORA, "Toradora!", _show_nfo(4224, "Toradora!"))

        assert result is not None, "папка спешлов осталась вовсе без привязки"
        assert result.series_id != 4283
        assert result.series_id in {9781, 9387, 9563}

    def test_lands_on_a_special_entry(self) -> None:
        _, result = _specials(TORADORA, "Toradora!", _show_nfo(4224, "Toradora!"))

        assert result is not None
        assert result.series_id in {9781, 9387, 9563}

    def test_web_series_is_not_mistaken_for_a_special(self) -> None:
        """`Mini Yuri` — тип `ona`, четыре серии. Это не спешл.

        Проверяем, куда попало, а не только куда не попало: иначе тест прошёл
        бы при пустом результате.
        """
        _, result = _specials(YURU_YURI, "Yuru Yuri", _show_nfo(10495, "Yuru Yuri"))

        assert result is not None
        assert result.series_id == 9888, "верный спешл франшизы"


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


class TestSpecialIsNamedInTheFileName:
    """Имя файла называет конкретный спешл — этого хватает, чтобы опознать.

    Настоящая пара с сервера: файл
    `Toradora! S00E05 - The True Meaning of Bento [Shiniori-Raws] Bluray-720p`
    и вариант названия `Toradora!: The True Meaning of Bento` у вхождения 9781.
    """

    FILE = "Toradora! S00E05 - The True Meaning of Bento [Shiniori-Raws] Bluray-720p"

    def test_picks_the_named_special(self) -> None:
        ctx = MatchContext(
            folder_name="Toradora!",
            scope="series",
            nfo=_show_nfo(4224, "Toradora!"),
            own_name="Specials",
            is_child=True,
            own_season=0,
            video_count=1,
            file_names=[self.FILE],
        )

        result = Matcher(TORADORA).match(ctx)

        assert result is not None
        assert result.series_id == 9781
        assert result.needs_confirmation is False, "название сошлось — подтверждать нечего"

    def test_without_file_names_it_stays_a_guess(self) -> None:
        """Без имён файлов выбирать не из чего — пометка обязана остаться."""
        ctx = MatchContext(
            folder_name="Toradora!",
            scope="series",
            nfo=_show_nfo(4224, "Toradora!"),
            own_name="Specials",
            is_child=True,
            own_season=0,
            video_count=1,
        )

        result = Matcher(TORADORA).match(ctx)

        assert result is not None
        assert result.needs_confirmation is True

    def test_spinoff_named_like_the_franchise_is_reachable(self) -> None:
        """`ona` вернули в рассмотрение — но найти его можно, только если его
        название начинается с названия франшизы.

        Кандидаты набираются поиском по префиксу, и это осознанная граница:
        `Mini Yuri` (тот самый спиноф Yuru Yuri) под неё **не** подпадает и по
        папке `Specials` не находится. Название из имени файла помогает выбрать
        среди найденных, но само по себе кандидатов не ищет.
        """
        index = TitleIndex(
            [
                series(260, "Yuru Yuri", year=2011, anilist=10495),
                series(
                    19093,
                    "Yuru Yuri OVA",
                    type_="ona",
                    year=2019,
                    episodes=1,
                ),
            ]
        )
        ctx = MatchContext(
            folder_name="Yuru Yuri",
            scope="series",
            nfo=_show_nfo(10495, "Yuru Yuri"),
            own_name="Specials",
            is_child=True,
            own_season=0,
            video_count=1,
            file_names=["Yuru Yuri S00E01 - OVA [Raws] WEBRip-720p"],
        )

        result = Matcher(index).match(ctx)

        assert result is not None
        assert result.series_id == 19093

    def test_spinoff_named_differently_is_not_found(self) -> None:
        """Обратная сторона той же границы — записана, чтобы её не забыть."""
        ctx = MatchContext(
            folder_name="Yuru Yuri",
            scope="series",
            nfo=_show_nfo(10495, "Yuru Yuri"),
            own_name="Specials",
            is_child=True,
            own_season=0,
            video_count=4,
            file_names=["Yuru Yuri S00E01 - Mini Yuri [Raws] WEBRip-720p"],
        )

        result = Matcher(YURU_YURI).match(ctx)

        # Привязки нет вовсе, и это верно — проверено усилением утверждения.
        #
        # `Mini Yuri` недостижим: его название не начинается с «Yuru Yuri», а
        # кандидатов набирает префикс. Единственный оставшийся кандидат —
        # спешл 9888 с **одним** эпизодом, и он отсекается проверкой
        # правдоподобия: в папке четыре видео. Отказ здесь лучше неверной
        # привязки, а папка уходит в список нераспознанных.
        assert result is None


class TestRealYuruYuriCase:
    """Замер, опровергнувший первоначальный порог.

    Файл `YuruYuri Happy Go Lily S00E01 - Love Paradox Eternal …` против
    вариантов названия вхождения 9888 даёт 64,9 — общее там только хвост. При
    пороге 70 верный ответ отвергался. Ближайший конкурент при этом набирает
    32,8, то есть лидер очевиден, и решать должен отрыв, а не абсолютная
    величина.

    Варианты названий настоящие, из ответа `/api/series/9888`.
    """

    INDEX = TitleIndex(
        [
            series(260, "Yuru Yuri", year=2011, anilist=10495),
            series(
                9888,
                "Yuru Yuri: Doushite, Tomaranai, Tokimeki, Dokidoki, Paradox, Eternal",
                type_="special",
                year=2011,
                episodes=1,
                extra=[
                    "YuruYuri: For Whatever Reason, Never Ending, "
                    "Heart Throbbing, Pitter-patter, Paradox, Eternal",
                    "Yuru Yuri Special",
                ],
            ),
            series(19093, "Yuru Yuri,", type_="ova", year=2019, episodes=1),
        ]
    )

    def test_picks_the_special_named_in_the_file(self) -> None:
        ctx = MatchContext(
            folder_name="Yuru Yuri",
            scope="series",
            nfo=_show_nfo(10495, "Yuru Yuri"),
            own_name="Specials",
            is_child=True,
            own_season=0,
            video_count=1,
            file_names=[
                "YuruYuri Happy Go Lily S00E01 - Love Paradox Eternal [Doki] Bluray-1080p"
            ],
        )

        result = Matcher(self.INDEX).match(ctx)

        assert result is not None
        assert result.series_id == 9888, "верный спешл отвергался слишком высоким порогом"
