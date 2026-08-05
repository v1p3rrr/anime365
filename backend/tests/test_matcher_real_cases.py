"""Случаи, снятые с живой библиотеки пользователя (372 папки, два корня).

Каждый тест здесь — конкретная неверная привязка, найденная разбором
2026-08-04 ([14-mapping-audit.md](../../claude/14-mapping-audit.md)). Данные
`.nfo` не выдуманы: поля взяты с сервера как есть.

Общее у всех этих ошибок одно: **источник совпадения решал всё, а данные —
ничего**. Нашёлся `anilistid` — 100% и никаких вопросов, даже если у папки
26 файлов, а у тайтла один эпизод.
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
    season: str | None = None,
    episodes: int = 12,
    mal: int | None = None,
    anilist: int | None = None,
    extra: list[str] | None = None,
) -> SeriesCandidate:
    return SeriesCandidate(
        id=sid,
        titles={"romaji": romaji},
        all_titles=[romaji, *(extra or [])],
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


class TestServiceFoldersDoNotInheritTheParent:
    """`Girls Band Cry`: восемь папок сели на один тайтл.

    Jellyfin кладёт `season.nfo` в **каждую** подпапку, включая `subtitles` и
    `ScreenShot`, и в служебных нет `<seasonnumber>`. Раньше этого хватало,
    чтобы принять `anilistid` родителя со стопроцентной уверенностью.
    """

    INDEX = TitleIndex(
        [
            series(33394, "Girls Band Cry", year=2024, season="Весна 2024", anilist=164212),
            series(40000, "Girls Band Cry Movie", type_="movie", year=2025, episodes=1),
        ]
    )
    PARENT = nfo(title="GIRLS BAND CRY", anilist_id=164212, year=2024)

    def _match(self, own_name: str, own_nfo: NfoData, videos: int = 0):
        return Matcher(self.INDEX).match(
            MatchContext(
                folder_name="Girls Band Cry",
                scope="series",
                nfo=self.PARENT,
                season_nfo=own_nfo,
                own_name=own_name,
                own_nfo=own_nfo,
                is_child=True,
                video_count=videos,
                local_episode_count=videos,
            )
        )

    def test_subtitles_folder_is_not_the_title(self) -> None:
        assert self._match("subtitles", nfo("season", title="subtitles")) is None

    def test_screenshot_folder_is_not_the_title(self) -> None:
        assert self._match("ScreenShot", nfo("season", title="ScreenShot")) is None

    def test_extras_folder_with_videos_is_not_the_title(self) -> None:
        """У `EXTRA` 39 видеофайлов — и это сканы дисков, а не сериал."""
        assert self._match("EXTRA", nfo("season", title="EXTRA"), videos=39) is None

    def test_real_season_still_maps(self) -> None:
        """Правку легко сделать слишком строгой: настоящий сезон обязан остаться."""
        result = self._match(
            "Season 1", nfo("season", title="Season 1", season=1, year=2024), videos=13
        )
        assert result is not None
        assert result.series_id == 33394


class TestBrokenContainerNfo:
    """`Evangelion`: `tvshow.nfo` описывает показ к 30-летию, а не сериал.

    Jellyfin сматчил папку-франшизу на показ 2026 года, и по наследству это
    уехало в `Evangelion/Season 1`, где лежат 26 серий сериала 1995 года.
    """

    INDEX = TitleIndex(
        [
            series(42114, "Evangelion: Housou 30-shuunen", type_="special", year=2026, episodes=1, anilist=204270),
            series(30, "Shinseiki Evangelion", year=1995, season="Осень 1995", episodes=26, mal=30),
        ]
    )
    PARENT = nfo(title="Evangelion: Housou 30 Shuunen", anilist_id=204270, shikimori_id=30, year=2026)

    def test_season_with_26_files_is_not_a_one_episode_screening(self) -> None:
        result = Matcher(self.INDEX).match(
            MatchContext(
                folder_name="Evangelion",
                scope="series",
                nfo=self.PARENT,
                season_nfo=nfo("season", title="Season 1", season=1),
                own_name="Season 1",
                own_nfo=nfo("season", title="Season 1", season=1),
                is_child=True,
                video_count=26,
                local_episode_count=26,
            )
        )
        # Показ с одним эпизодом отвергнут; принят либо сериал, либо ничего —
        # но только не тайтл, в который эти файлы физически не помещаются.
        assert result is None or result.series_id == 30


class TestWrongExternalIdInCatalog:
    """`Your Name`: разметка Jellyfin называет фильм рекламой минеральной воды.

    ⚠️ **Чинить чужую ошибку идентификации — не наша задача**, и это решение
    пользователя: у него в Jellyfin и название было про воду, он поправил
    вручную, там и место таким правкам. Никакая эвристика с нашей стороны не
    должна «знать лучше» разметки — так ломаются верные случаи.

    Наша часть другая: **не усиливать чужую ошибку и не прятать её**. Повторить
    неверную привязку с уверенностью 100% значит спрятать: такую папку не
    найти, пока файлы не уедут не туда. Поэтому берём кандидата, который
    сходится с названием из того же файла, и обязательно помечаем «ждёт
    подтверждения» — это сигнал сходить и поправить **в Jellyfin**.
    """

    INDEX = TitleIndex(
        [
            series(9000, "Suntory Minami Alps no Tennensui", type_="cm", year=2013, episodes=3, anilist=97962),
            series(9001, "Kimi no Na wa.", type_="movie", year=2016, episodes=1, mal=32281, extra=["Your Name"]),
        ]
    )

    def test_id_pointing_at_a_water_commercial_is_refused(self) -> None:
        result = Matcher(self.INDEX).match(
            MatchContext(
                folder_name="Your Name",
                scope="movie",
                nfo=nfo("movie", title="Your Name", anilist_id=97962, shikimori_id=32281, year=2016),
                own_name="Your Name",
                own_nfo=nfo("movie", title="Your Name", anilist_id=97962, shikimori_id=32281, year=2016),
                video_count=1,
                local_episode_count=1,
            )
        )
        assert result is not None
        assert result.series_id == 9001, "должен победить кандидат, чьё название сходится"
        assert result.needs_confirmation, (
            "разошлись с разметкой Jellyfin — привязку обязаны пометить, "
            "а не поставить молча"
        )


class TestDifferentRomanizationSurvives:
    """Контрпример: ужесточение порогов не должно ломать верные привязки.

    `Genshiken/Season 1` сел на «Gendai Shikaku Bunka Kenkyuu Kai» — похожесть
    по имени папки всего 44, и это **правильный** матч: другая романизация того
    же названия. Спасает то, что сверяемся со всеми написаниями каталога.
    """

    def test_alternate_romanization_is_kept(self) -> None:
        index = TitleIndex(
            [
                series(
                    5000,
                    "Gendai Shikaku Bunka Kenkyuu Kai",
                    year=2004,
                    episodes=12,
                    anilist=655,
                    extra=["Genshiken"],
                )
            ]
        )
        result = Matcher(index).match(
            MatchContext(
                folder_name="Genshiken",
                scope="series",
                nfo=nfo(title="Genshiken", anilist_id=655, year=2004),
                season_nfo=nfo("season", title="Season 1", season=1),
                own_name="Season 1",
                own_nfo=nfo("season", title="Season 1", season=1),
                is_child=True,
                video_count=12,
                local_episode_count=12,
            )
        )
        assert result is not None
        assert result.series_id == 5000


class TestSeasonsDoNotCollapse:
    """`Monogatari/Season 4` садился на первый сезон по точному названию."""

    def test_fourth_season_does_not_take_the_first(self) -> None:
        index = TitleIndex(
            [
                series(1032, "Bakemonogatari", year=2009, season="Лето 2009", episodes=15),
                series(1040, "Monogatari Series: Second Season", year=2013, season="Лето 2013"),
                series(1050, "Owarimonogatari", year=2015, season="Осень 2015", episodes=12),
            ]
        )
        result = Matcher(index).match(
            MatchContext(
                folder_name="Monogatari",
                scope="series",
                nfo=nfo(title="Monogatari"),
                season_nfo=nfo("season", title="Season 4", season=4, premiered="2015-10-04"),
                own_name="Season 4",
                own_nfo=nfo("season", title="Season 4", season=4, premiered="2015-10-04"),
                is_child=True,
                video_count=12,
                local_episode_count=12,
            )
        )
        # ⚠️ Положительно: «`None` или не первый сезон» прошло бы и при
        # матчере, не находящем вообще ничего.
        assert result is not None, "четвёртый сезон остался без привязки"
        assert result.series_id != 1032, "первый сезон 2009 года — не четвёртый"


class TestNfoAboutAnotherTitle:
    """`.nfo` в папке описывает совсем другое аниме.

    Снято с сервера 2026-08-05 — это оказалось причиной «большинство фильмов
    вообще не впопад»:

        папка «A Silent Voice - The Movie» → <title>Meitantei Conan Magic File 2…
        папка «Look Back»                  → <title>Kekkai Sensen & Beyond…
        папка «Cencoroll Connect»          → <title>Connected…

    Jellyfin записал в папки метаданные другого тайтла, и матчер честно
    приводил к тому, что там написано — совпадение выходило **точным**, 99%.

    Знать, какое аниме правильное, мы не можем. Но заметить, что имя папки и
    `.nfo` противоречат друг другу, — можем.
    """

    INDEX = TitleIndex(
        [
            series(1, "Koe no Katachi", type_="movie", year=2016, episodes=1,
                   extra=["A Silent Voice", "A Silent Voice: The Movie"]),
            series(2, "Meitantei Conan Magic File 2: Kudou Shinichi", type_="special",
                   year=2008, episodes=1),
        ]
    )

    def _match(self, folder: str, nfo_title: str, ids: dict | None = None):
        data = nfo("tvshow", title=nfo_title, **(ids or {}))
        return Matcher(self.INDEX).match(
            MatchContext(
                folder_name=folder,
                scope="movie",
                nfo=data,
                own_name=folder,
                own_nfo=data,
                video_count=1,
                local_episode_count=1,
            )
        )

    def test_foreign_nfo_does_not_win(self) -> None:
        result = self._match(
            "A Silent Voice - The Movie",
            "Meitantei Conan Magic File 2: Kudou Shinichi Nazo no Kabe to Kuro Lab Jiken",
        )
        assert result is not None, "папка осталась без привязки вовсе"
        assert result.series_id != 2, "Конан не должен победить"

    def test_folder_name_is_used_instead(self) -> None:
        # Полное название, как в настоящем `.nfo`: по нему тайтл находится
        # точно, и расхождение с папкой становится доказуемым. По обрезанному
        # («Meitantei Conan Magic File 2») каталог уверенно ничего не находит —
        # и тогда мы молчим, а не гадаем.
        result = self._match(
            "A Silent Voice - The Movie", "Meitantei Conan Magic File 2: Kudou Shinichi"
        )
        assert result is not None
        assert result.series_id == 1, "искать надо по имени папки, раз .nfo о другом"
        assert result.needs_confirmation, "догадку обязаны показать человеку"

    def test_agreeing_nfo_is_trusted(self) -> None:
        """Правку легко сделать слишком подозрительной: согласный .nfo обязан работать."""
        result = self._match("A Silent Voice - The Movie", "A Silent Voice: The Movie")
        assert result is not None
        assert result.series_id == 1

    def test_different_wording_is_not_a_disagreement(self) -> None:
        """«Yuru Camp» против «Laid-Back Camp» — то же аниме, разные подписи.

        Порог низкий намеренно: придираться к разным написаниям нельзя, ловим
        только явное расхождение.
        """
        index = TitleIndex(
            [series(10, "Yuru Camp△", year=2018, extra=["Laid-Back Camp"])]
        )
        data = nfo("tvshow", title="Laid-Back Camp", anilist_id=98444)
        result = Matcher(index).match(
            MatchContext(
                folder_name="Yuru Camp",
                scope="series",
                nfo=data,
                own_name="Yuru Camp",
                own_nfo=data,
                video_count=12,
                local_episode_count=12,
            )
        )
        assert result is not None
        assert result.series_id == 10


class TestRemakeSharesTheJapaneseTitle:
    """`Fullmetal Alchemist - Brotherhood/Season 1`: 64 файла уехали на 2003 год.

    Снято с сервера: папка тайтла привязалась к «Hagane no Renkinjutsushi (2009)»
    по `anilistid` со стопроцентной уверенностью, а её сезонная подпапка **со
    всеми файлами** — к сериалу 2003 года по японскому названию с уверенностью
    91. На странице Brotherhood при этом не показывалось ни одной скачанной
    серии.

    Причина: 鋼の錬金術師 носят обе записи, и строка их не различает в принципе.
    Различает идентификатор, и он лежит в том же `.nfo`.
    """

    INDEX = TitleIndex(
        [
            series(
                4389,
                "Fullmetal Alchemist",
                year=2003,
                episodes=51,
                anilist=121,
                extra=["鋼の錬金術師"],
            ),
            series(
                2400,
                "Hagane no Renkinjutsushi (2009)",
                year=2009,
                episodes=64,
                anilist=5114,
                extra=["鋼の錬金術師"],
            ),
        ]
    )

    @staticmethod
    def _index_with_japanese() -> TitleIndex:
        """`titles.ja` заполняется отдельно — по нему и строится индекс."""
        items = []
        for item in TestRemakeSharesTheJapaneseTitle.INDEX.by_id.values():
            item.titles = {**item.titles, "ja": "鋼の錬金術師"}
            items.append(item)
        return TitleIndex(items)

    def _season_folder(self) -> MatchContext:
        show = nfo(title="Fullmetal Alchemist: Brotherhood", anilist_id=5114)
        show.original_title = "鋼の錬金術師"
        return MatchContext(
            folder_name="Fullmetal Alchemist - Brotherhood",
            scope="series",
            nfo=show,
            season_nfo=nfo("season", season=1),
            own_name="Season 1",
            is_child=True,
            video_count=64,
            local_episode_count=64,
        )

    def test_season_folder_follows_the_id_not_the_japanese_title(self) -> None:
        result = Matcher(self._index_with_japanese()).match(self._season_folder())

        assert result is not None
        assert result.series_id == 2400, "64 файла уехали на сериал 2003 года"

    def test_japanese_title_alone_still_works(self) -> None:
        """Страховка: без неоднозначности каскад работает как прежде."""
        index = TitleIndex(
            [
                series(2400, "Hagane no Renkinjutsushi (2009)", year=2009, episodes=64),
            ]
        )
        for item in index.by_id.values():
            item.titles = {**item.titles, "ja": "鋼の錬金術師"}
        index = TitleIndex(list(index.by_id.values()))

        show = nfo(title="Fullmetal Alchemist: Brotherhood")
        show.original_title = "鋼の錬金術師"
        ctx = MatchContext(
            folder_name="Fullmetal Alchemist - Brotherhood",
            scope="series",
            nfo=show,
            own_name="Fullmetal Alchemist - Brotherhood",
            is_child=False,
            video_count=64,
        )

        result = Matcher(index).match(ctx)
        assert result is not None and result.series_id == 2400
