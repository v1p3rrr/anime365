"""Механизм `.nfo` работает на любой письменности, а не только на японской.

Пользователь сменил у AniList-плагина Jellyfin настройку названий: основное
стало английским, дополнительное — ромадзи. До этого в `<originaltitle>` лежало
японское, и механизм искал строку **только** среди японских названий каталога.

Замерено на его библиотеке (450 папок с `.nfo`):

* `<originaltitle>` уже у 46 папок латиницей, у 404 — иероглифами;
* `<title>` не бывает японским **ни у одной**;
* смесь не переходная: латиница в `<originaltitle>` встречалась и до смены
  настройки — там просто отработал другой источник метаданных;
* каждая строка — полноценный ключ: английское название нашлось в каталоге у
  402 папок из 450, ромадзи есть у 29 169 тайтлов каталога из 29 231.

Поэтому три раскладки ниже обязаны давать один и тот же тайтл. Данные взяты с
сервера как есть.
"""

from __future__ import annotations

import pytest

from app.services.matcher import (
    MatchContext,
    MatchSource,
    Matcher,
    SeriesCandidate,
    TitleIndex,
    looks_japanese,
)
from app.services.nfo_parser import NfoData

#: Тайтл 23834 каталога — «Красавчики-детективы».
SERIES_ID = 23834
ROMAJI = "Bishounen Tanteidan"
JAPANESE = "美少年探偵団"
ENGLISH = "Pretty Boy Detective Club"

INDEX = TitleIndex(
    [
        SeriesCandidate(
            id=SERIES_ID,
            titles={"romaji": ROMAJI, "ja": JAPANESE},
            all_titles=[ROMAJI, JAPANESE, ENGLISH],
            type="tv",
            year=2021,
            season="Весна 2021",
            number_of_episodes=12,
            mal_id=None,
            anilist_id=None,
        )
    ]
)


def _folder(title: str, original_title: str) -> MatchContext:
    """Папка с `.nfo`, но **без единого внешнего идентификатора**.

    Идентификаторов нет намеренно: с ними сработал бы каскад A1, и проверка
    названий ничего бы не доказала.
    """
    show = NfoData(kind="tvshow", path="tvshow.nfo")
    show.title = title
    show.original_title = original_title
    return MatchContext(
        folder_name=ENGLISH,
        scope="series",
        nfo=show,
        own_name=ENGLISH,
        is_child=False,
        video_count=12,
        local_episode_count=12,
    )


class TestScriptDetection:
    def test_kana_and_kanji_are_japanese(self) -> None:
        assert looks_japanese(JAPANESE)
        assert looks_japanese("ゆるゆり")
        assert looks_japanese("化物語")

    def test_latin_is_not(self) -> None:
        assert not looks_japanese(ROMAJI)
        assert not looks_japanese(ENGLISH)
        assert not looks_japanese("")

    def test_a_mixed_name_counts_as_japanese(self) -> None:
        """`NANA-ナナ-` и `16bitセンセーション` — японские названия с латиницей."""
        assert looks_japanese("NANA-ナナ-")
        assert looks_japanese("16bitセンセーション ANOTHER LAYER")


class TestThreeLayouts:
    """Одна и та же папка, три состояния `.nfo` — один и тот же тайтл."""

    @pytest.mark.parametrize(
        ("label", "title", "original"),
        [
            ("ромадзи + иероглифы (как было)", ROMAJI, JAPANESE),
            ("английский + ромадзи (как стало)", ENGLISH, ROMAJI),
            ("английский + иероглифы (другой источник)", ENGLISH, JAPANESE),
        ],
    )
    def test_layout_resolves_to_the_same_title(
        self, label: str, title: str, original: str
    ) -> None:
        result = Matcher(INDEX).match(_folder(title, original))

        assert result is not None, f"{label}: тайтл не нашёлся"
        assert result.series_id == SERIES_ID, label

    def test_an_empty_original_title_falls_through_to_the_title(self) -> None:
        """У одной папки из 450 `<originaltitle>` пуст — читаем следующее поле."""
        result = Matcher(INDEX).match(_folder(ROMAJI, ""))

        assert result is not None
        assert result.series_id == SERIES_ID


class TestTheMechanismItself:
    """Проверка самого механизма, а не итога сверки.

    ⚠️ По итогу `match()` этого не увидеть: имя папки участвует в строковом
    каскаде, тот даёт точное совпадение с уверенностью 99 и перебивает
    предложение механизма `.nfo` с его 97. Тайтл при этом один и тот же, то
    есть снаружи всё в порядке, — но тогда тест проходил бы и со старым кодом,
    ничего не доказывая. Поэтому зовём механизм напрямую.
    """

    @staticmethod
    def _proposals(title: str, original: str):
        ctx = _folder(title, original)
        return Matcher(INDEX)._propose_nfo_titles(ctx, ctx.nfo)

    @pytest.mark.parametrize(
        ("label", "title", "original"),
        [
            ("ромадзи + иероглифы (как было)", ROMAJI, JAPANESE),
            ("английский + ромадзи (как стало)", ENGLISH, ROMAJI),
            ("английский + иероглифы (другой источник)", ENGLISH, JAPANESE),
        ],
    )
    def test_every_layout_gives_a_proposal(
        self, label: str, title: str, original: str
    ) -> None:
        found = self._proposals(title, original)

        assert found, f"{label}: механизм .nfo не предложил ничего"
        assert found[0].series_id == SERIES_ID
        assert found[0].source is MatchSource.NFO_TITLE

    def test_latin_string_is_not_looked_up_among_japanese_titles(self) -> None:
        """Ровно то, что ломалось: латиница в японском индексе не находится.

        Старый механизм искал `<originaltitle>` только там — и на новой
        раскладке замолкал.
        """
        assert INDEX.by_japanese.get(ROMAJI.lower()) is None
        assert self._proposals(ENGLISH, ROMAJI), "латинская строка должна искаться в общем индексе"
