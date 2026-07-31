"""Приведение языков дорожек к двухбуквенным кодам."""

from __future__ import annotations

import pytest

from app.services.languages import language_name, normalize_lang


class TestNoDuplicates:
    """Главное требование: одно написание — один код.

    Ради этого и заведена библиотека. Ручной словарь не задваивал `ja`/`jpn`
    только потому, что всё незнакомое молча выбрасывал.
    """

    @pytest.mark.parametrize(
        "variants,expected",
        [
            (["ja", "jpn", "JPN", " jpn ", "Japanese", "japanese"], "ja"),
            (["ru", "rus", "RUS", "Russian"], "ru"),
            (["en", "eng", "English"], "en"),
            # ⚠️ Вот где ручной словарь и ломался: у языка два трёхбуквенных
            # кода — библиографический и терминологический.
            (["de", "ger", "deu"], "de"),
            (["fr", "fre", "fra"], "fr"),
            (["zh", "chi", "zho"], "zh"),
            (["cs", "cze", "ces"], "cs"),
            (["el", "gre", "ell"], "el"),
            (["nl", "dut", "nld"], "nl"),
            (["is", "ice", "isl"], "is"),
            (["fa", "per", "fas"], "fa"),
        ],
    )
    def test_all_spellings_collapse_to_one_code(self, variants, expected):
        assert {normalize_lang(v) for v in variants} == {expected}


class TestLanguagesThatUsedToBeLost:
    """Раньше возвращалось None для всего, кроме ru/ja/en."""

    @pytest.mark.parametrize(
        "raw,expected",
        [("fre", "fr"), ("spa", "es"), ("ita", "it"), ("kor", "ko"), ("por", "pt")],
    )
    def test_other_languages_survive(self, raw, expected):
        assert normalize_lang(raw) == expected


class TestNonStandardSpellings:
    def test_jap_means_japanese_here(self):
        # ⚠️ По ISO `jap` — это жаруара (Бразилия), устаревший с 2007 года.
        # Библиотека на нём бросает исключение и `ja` не даст никогда.
        # В аниме-релизах так пишут именно японский, и терять дорожку хуже,
        # чем отступить от стандарта. Отступление осознанное.
        assert normalize_lang("jap") == "ja"

    def test_locale_suffixes(self):
        assert normalize_lang("jpn-JP") == "ja"
        assert normalize_lang("eng-US") == "en"


class TestNoLanguage:
    @pytest.mark.parametrize("raw", [None, "", "   ", "und", "zxx", "mul", "unknown"])
    def test_absent_language_is_none_not_an_error(self, raw):
        # Дорожка без языка — обычное дело, а не сбой разбора.
        assert normalize_lang(raw) is None

    def test_garbage_is_none(self):
        assert normalize_lang("xyz") is None
        assert normalize_lang("не язык") is None


class TestRareLanguages:
    def test_language_without_two_letter_code_keeps_three(self):
        # Двухбуквенный код есть лишь у 183 языков из 8038. Для остальных
        # показать длинный код лучше, чем потерять язык.
        assert normalize_lang("haw") == "haw"  # гавайский
        assert normalize_lang("ain") == "ain"  # айнский


class TestLanguageName:
    def test_name_for_tooltip(self):
        assert language_name("ja") == "Japanese"
        assert language_name("fr") == "French"

    def test_unknown_has_no_name(self):
        assert language_name("xyz") is None
        assert language_name(None) is None
