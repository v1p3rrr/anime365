"""Регрессия парсера имён по корпусу из реальной библиотеки.

Случаи лежат в tests/filenames.yaml. Правило добавляется только вместе со случаем:
иначе новое правило однажды тихо сломает уже разобранное.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.services.filename_rules import (
    load_rules,
    normalize_for_match,
    normalize_title,
    parse_filename,
    strip_language_suffix,
)

CORPUS = Path(__file__).parent / "filenames.yaml"
RULES_CONFIG = Path(__file__).resolve().parents[2] / "config" / "filename_rules.yaml"


@pytest.fixture(scope="module")
def rules():
    return load_rules(RULES_CONFIG)


def _corpus() -> list[dict]:
    return yaml.safe_load(CORPUS.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _corpus(), ids=lambda c: c["name"])
def test_corpus(case: dict, rules) -> None:
    result = parse_filename(case["file"], case.get("scope", "any"), rules)
    expected = case.get("expect")

    if expected is None:
        assert not result.is_parsed or result.excluded_by, (
            f"{case['file']!r} не должен разбираться как серия, "
            f"но правило {result.matched_rule_id} дало серию {result.episode}"
        )
        return

    assert not result.excluded_by, f"{case['file']!r} ошибочно исключён"
    assert result.matched_rule_id == expected["rule"]

    for field in ("season", "episode", "episode_end", "year", "translation_id"):
        if field in expected:
            actual = getattr(result, field)
            actual = int(actual) if isinstance(actual, float) else actual
            assert actual == expected[field], f"{field}: ожидалось {expected[field]}, got {actual}"

    if "language" in expected:
        assert result.language == expected["language"]
    if "group" in expected:
        assert result.release_group == expected["group"]


class TestLanguageSuffix:
    def test_plain_language(self, rules) -> None:
        assert strip_language_suffix("Show S01E01.ru", rules) == ("Show S01E01", "ru")

    def test_alias_is_normalized(self, rules) -> None:
        # В библиотеке рядом лежат .jpn.srt и .ja.srt — оба должны стать ja.
        assert strip_language_suffix("Movie.jpn", rules)[1] == "ja"
        assert strip_language_suffix("Movie.eng", rules)[1] == "en"

    def test_numeric_chunk_is_dropped(self, rules) -> None:
        # Реальный случай: "... [JA][JA].3.ja.srt"
        assert strip_language_suffix("Movie.3.ja", rules) == ("Movie", "ja")

    def test_no_suffix(self, rules) -> None:
        assert strip_language_suffix("Movie", rules) == ("Movie", None)


class TestNormalization:
    def test_plus_is_treated_as_slash(self) -> None:
        # В именах папок "/" запрещён, поэтому его заменяют на "+".
        assert normalize_title("Fate+stay night") == normalize_title("Fate/stay night")

    def test_unicode_roman_numeral(self) -> None:
        # movie.nfo содержит "Ⅰ.presage flower" юникодной римской цифрой.
        assert normalize_title("Ⅰ.presage flower") == normalize_title("I.presage flower")

    def test_decorative_symbols_are_dropped(self) -> None:
        assert normalize_title("Yuru Yuri San☆Hai!") == "yuru yuri san hai"
        assert normalize_title("Yuru Camp△") == "yuru camp"

    def test_diacritics_are_folded(self) -> None:
        assert normalize_title("Pokémon") == normalize_title("Pokemon")

    def test_season_is_extracted_not_dropped(self) -> None:
        # Номер сезона должен уходить отдельным признаком: иначе "Yuru Yuri♪♪"
        # станет неотличим от "Yuru Yuri".
        assert normalize_for_match("Yuru Yuri♪♪") == ("yuru yuri", 2)
        assert normalize_for_match("Sousou no Frieren 2nd Season") == ("sousou no frieren", 2)
        assert normalize_for_match("[Ohys-Raws] Yuru Camp Season 2 (BD 1280x720)") == (
            "yuru camp",
            2,
        )

    def test_technical_noise_is_stripped(self) -> None:
        norm, _ = normalize_for_match("Watashi wo Tabetai, Hitodenashi (AT-X 1920x1080 x265 AAC)")
        assert norm == "watashi wo tabetai hitodenashi"


class TestRulesAreShippedWithImage:
    """Правила разбора имён обязаны быть доступны без bind-mount на ./config.

    Контейнер, запущенный `docker run` без монтирования конфига, падал при
    сканировании: запасной путь `parents[3]` внутри образа разворачивается
    в тот же /config, которого там нет.
    """

    def test_source_resolves_without_config_mount(self) -> None:
        from app.services.filename_rules import _resolve_source

        # В разработке это репозиторный config, в образе — копия внутри пакета.
        assert _resolve_source().is_file()

    def test_candidates_include_packaged_copy(self) -> None:
        from app.services import filename_rules as fr

        # Порядок важен: пользовательский override перекрывает копию в образе.
        assert fr.CONFIG_PATH == Path("/config/filename_rules.yaml")
        assert fr.PACKAGED_PATH.name == "filename_rules.yaml"
        assert "defaults" in fr.PACKAGED_PATH.parts

    def test_rules_actually_load(self) -> None:
        from app.services.filename_rules import load_rules

        rules = load_rules()
        assert rules.rules, "без правил разбор имён не работает вовсе"
        assert ".mkv" in rules.video_ext
