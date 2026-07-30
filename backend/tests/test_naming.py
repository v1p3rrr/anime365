"""Тесты правил именования.

Образцы взяты из реальной библиотеки. Правила задал пользователь явно, поэтому
каждое зафиксировано отдельным тестом: молчаливое изменение схемы именования
рассыпет библиотеку.
"""

from __future__ import annotations

import pytest

from app.services.naming import (
    NamingContext,
    build_target_name,
    canonical_name,
    name_from_neighbour,
    pick_neighbour,
    sanitize_author,
    sanitize_component,
    subtitle_name_for_video,
    truncate_to_bytes,
    unique_path,
)

VIDEO_EXT = {".mkv", ".mp4", ".avi"}


def ctx(**kw) -> NamingContext:
    defaults = dict(
        show_title="Zombie Land Saga",
        season=2,
        episode=5,
        episode_title="Little Bodda Bope SAGA",
        authors="SebiVoice",
        height=1080,
        lang="ru",
        kind="voice",
        extension=".mp4",
    )
    defaults.update(kw)
    return NamingContext(**defaults)


class TestSanitize:
    def test_forbidden_characters_removed(self) -> None:
        assert "/" not in sanitize_component("Fate/stay night")
        assert ":" not in sanitize_component("Title: Subtitle")

    def test_brackets_removed(self) -> None:
        # Квадратные скобки сломали бы последующий разбор релиз-группы.
        assert "[" not in sanitize_component("Crunchyroll [10bit]")

    def test_reserved_windows_name(self) -> None:
        # Библиотека может лежать на SMB-шаре.
        assert sanitize_component("CON") == "_CON"
        assert sanitize_component("COM1") == "_COM1"

    def test_trailing_dots_and_spaces(self) -> None:
        assert sanitize_component("Your Name. ") == "Your Name"

    def test_author_spaces_become_underscores(self) -> None:
        assert sanitize_author("YakuSub Studio (Aero)") == "YakuSub_Studio_(Aero)"

    def test_author_with_brackets(self) -> None:
        # Реальное значение из API.
        assert sanitize_author("Crunchyroll [10bit]") == "Crunchyroll_10bit"

    def test_empty_author_has_fallback(self) -> None:
        assert sanitize_author("") == "unknown"

    def test_control_characters_stripped(self) -> None:
        assert "\x00" not in sanitize_component("bad\x00name")


class TestByteLimit:
    def test_cyrillic_counted_in_bytes(self) -> None:
        # В ext4 предел 255 БАЙТ, а кириллица в UTF-8 двухбайтовая.
        long_name = "Я" * 200 + ".mkv"
        result = truncate_to_bytes(long_name, limit=100)
        assert len(result.encode("utf-8")) <= 100
        assert result.endswith(".mkv")

    def test_short_name_untouched(self) -> None:
        assert truncate_to_bytes("short.mkv") == "short.mkv"


class TestCanonicalName:
    def test_series_name(self) -> None:
        name = canonical_name(ctx())
        assert name == (
            "Zombie Land Saga S02E05 - Little Bodda Bope SAGA "
            "[anime365_SebiVoice] WEBDL-1080p.ru.mp4"
        )

    def test_raw_has_no_language_suffix(self) -> None:
        # Японский оригинал — суффикс не добавляем.
        name = canonical_name(ctx(kind="raw", lang="ja"))
        assert ".ja." not in name
        assert name.endswith("WEBDL-1080p.mp4")

    def test_english_voice_suffix(self) -> None:
        assert ".en." in canonical_name(ctx(kind="voice", lang="en"))

    def test_subtitle_language_suffix(self) -> None:
        assert ".ja." in canonical_name(ctx(kind="sub", lang="ja", extension=".ass"))

    def test_movie_scheme(self) -> None:
        name = canonical_name(
            ctx(show_title="Your Name.", is_movie=True, year=2016, episode=1, episode_title=None)
        )
        assert name.startswith("Your Name (2016)")
        assert "WEBDL-1080p" in name

    def test_fractional_episode(self) -> None:
        # Спешлы между сериями нумеруются дробно.
        assert "S02E5_5" in canonical_name(ctx(episode=5.5))


class TestNeighbourName:
    NEIGHBOUR = "Zombie Land Saga S02E05 - Little Bodda Bope SAGA [neoHEVC] Bluray-1080p.mkv"

    def test_release_group_replaced(self) -> None:
        name = name_from_neighbour(self.NEIGHBOUR, ctx())
        assert "[anime365_SebiVoice]" in name
        assert "neoHEVC" not in name

    def test_source_tag_always_webdl(self) -> None:
        # Ключевое требование: у соседа Bluray, у нас всегда WEBDL.
        name = name_from_neighbour(self.NEIGHBOUR, ctx())
        assert "WEBDL-1080p" in name
        assert "Bluray" not in name

    def test_structure_preserved(self) -> None:
        name = name_from_neighbour(self.NEIGHBOUR, ctx())
        assert name.startswith("Zombie Land Saga S02E05 - Little Bodda Bope SAGA")

    def test_neighbour_episode_title_never_copied(self) -> None:
        """Название эпизода берётся своё, а не соседское.

        Иначе серия 6 получала бы заголовок серии 5 — так и было до исправления.
        """
        name = name_from_neighbour(self.NEIGHBOUR, ctx(episode=6, episode_title="Own Title"))
        assert "S02E06" in name
        assert "Own Title" in name
        assert "Little Bodda Bope" not in name

    def test_no_title_means_no_title(self) -> None:
        # Anime365 отдаёт episodeTitle пустым почти всегда: лучше без названия,
        # чем с чужим.
        name = name_from_neighbour(self.NEIGHBOUR, ctx(episode=6, episode_title=None))
        assert "Little Bodda Bope" not in name
        assert name.startswith("Zombie Land Saga S02E06 [anime365_")

    def test_episode_number_is_ours_not_neighbours(self) -> None:
        name = name_from_neighbour(self.NEIGHBOUR, ctx(episode=7))
        assert "S02E07" in name
        assert "S02E05" not in name

    def test_language_suffix_added(self) -> None:
        assert name_from_neighbour(self.NEIGHBOUR, ctx()).endswith(".ru.mp4")

    def test_unparseable_neighbour_returns_none(self) -> None:
        assert name_from_neighbour("random file without structure.mkv", ctx()) is None

    def test_neighbour_language_suffix_not_duplicated(self) -> None:
        name = name_from_neighbour(
            "Zombie Land Saga S02E05 - Title [neoHEVC] Bluray-1080p.ja.srt", ctx()
        )
        assert name is not None
        assert name.count(".ru") == 1
        assert ".ja" not in name


class TestSubtitleForExistingVideo:
    VIDEO = "Zombie Land Saga S02E05 - Little Bodda Bope SAGA [neoHEVC] Bluray-1080p.mkv"

    def test_basename_preserved_exactly(self) -> None:
        """Имя обязано совпасть с видео, иначе Jellyfin не подхватит субтитры."""
        name = subtitle_name_for_video(self.VIDEO, "YakuSub Studio", "ru", ".ass")
        assert name.startswith(
            "Zombie Land Saga S02E05 - Little Bodda Bope SAGA [neoHEVC] Bluray-1080p."
        )

    def test_foreign_source_tag_kept(self) -> None:
        # Правило про WEBDL сюда НЕ применяется: это чужое имя, менять его нельзя.
        name = subtitle_name_for_video(self.VIDEO, "YakuSub", "ru", ".ass")
        assert "Bluray-1080p" in name
        assert "WEBDL" not in name

    def test_foreign_release_group_kept(self) -> None:
        assert "neoHEVC" in subtitle_name_for_video(self.VIDEO, "YakuSub", "ru", ".ass")

    def test_author_allows_multiple_translations(self) -> None:
        first = subtitle_name_for_video(self.VIDEO, "YakuSub", "ru", ".ass")
        second = subtitle_name_for_video(self.VIDEO, "AniLibria", "ru", ".ass")
        assert first != second

    def test_language_suffix_last(self) -> None:
        name = subtitle_name_for_video(self.VIDEO, "YakuSub", "ja", ".ass")
        assert name.endswith(".ja.ass")


class TestPickNeighbour:
    def test_ignores_non_video(self) -> None:
        files = ["poster.jpg", "tvshow.nfo", "Show S01E01 - Title [Doki] Bluray-1080p.mkv"]
        assert pick_neighbour(files, VIDEO_EXT) == files[2]

    def test_ignores_unparseable(self) -> None:
        files = ["random.mkv", "Show S01E01 - Title [Doki] Bluray-1080p.mkv"]
        assert pick_neighbour(files, VIDEO_EXT) == files[1]

    def test_none_when_nothing_suitable(self) -> None:
        assert pick_neighbour(["poster.jpg", "random.mkv"], VIDEO_EXT) is None


class TestBuildTargetName:
    def test_subtitle_to_existing_video_wins(self) -> None:
        """Сценарий с существующим видео имеет приоритет над образцом."""
        name = build_target_name(
            ctx(kind="sub", extension=".ass"),
            neighbour="Other S01E01 - X [Group] Bluray-1080p.mkv",
            existing_video="Zombie Land Saga S02E05 - Title [neoHEVC] Bluray-1080p.mkv",
        )
        assert "neoHEVC" in name
        assert "Bluray" in name

    def test_falls_back_to_canonical(self) -> None:
        name = build_target_name(ctx(), neighbour="unparseable.mkv")
        assert "WEBDL-1080p" in name
        assert "[anime365_SebiVoice]" in name


class TestUniquePath:
    def test_no_collision(self) -> None:
        assert unique_path("/x", "file.mkv", set()) == "file.mkv"

    def test_collision_gets_version(self) -> None:
        assert unique_path("/x", "file.mkv", {"file.mkv"}) == "file v2.mkv"

    def test_multiple_collisions(self) -> None:
        existing = {"file.mkv", "file v2.mkv", "file v3.mkv"}
        assert unique_path("/x", "file.mkv", existing) == "file v4.mkv"


@pytest.mark.parametrize(
    ("kind", "lang", "expected"),
    [
        ("voice", "ru", ".ru"),
        ("voice", "en", ".en"),
        ("raw", "ja", ""),
        ("sub", "ru", ".ru"),
        ("sub", "ja", ".ja"),
        ("sub", "en", ".en"),
    ],
)
def test_language_suffix_table(kind: str, lang: str, expected: str) -> None:
    name = canonical_name(ctx(kind=kind, lang=lang, extension=".mp4"))
    assert name.endswith(f"{expected}.mp4")
