"""Тесты правил именования.

Образцы взяты из реальной библиотеки. Правила задал пользователь явно, поэтому
каждое зафиксировано отдельным тестом: молчаливое изменение схемы именования
рассыпет библиотеку.
"""

from __future__ import annotations

import pytest

from app.services.filename_rules import get_rules, parse_filename
from app.services.naming import (
    NamingContext,
    audio_name,
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


class TestLongNamesKeepTheirTail:
    """Предел ext4 — 255 **байт** на имя, и режется не хвост, а название.

    ⚠️ Хвост несёт всё, что имя сообщает машине: код серии, метку группы с
    номером перевода, ступень качества и языковой суффикс. Обрезка с конца
    съедала их первыми — то есть у самых длинных названий Jellyfin переставал
    понимать язык субтитров. Это ломалось и до появления номера перевода.

    Сегодня запас есть: самое длинное имя в библиотеке пользователя — 175 байт
    из 13 446 файлов. Но у самого длинного romaji в каталоге 232 символа, и на
    нём предел достигается.
    """

    LONG = "Tsuihou sareta Cheat Fuyo Majutsushi wa Kimama na Second Life wo Ouka suru. " * 3

    def _long(self, **kw):
        return ctx(
            show_title=self.LONG,
            episode_title="Ougi Formula and the Longest Night of the Whole Season",
            authors="Seiya & Surokune & Андрей Зайцев & кто-то ещё",
            **kw,
        )

    def test_it_fits_the_limit(self) -> None:
        name = canonical_name(self._long(translation_id=4505262))
        assert len(name.encode("utf-8")) <= 250

    def test_episode_code_survives(self) -> None:
        """Без него файл не собрать обратно ни нам, ни Jellyfin."""
        assert "S02E05" in canonical_name(self._long(translation_id=4505262))

    def test_translation_id_survives(self) -> None:
        assert "_t4505262]" in canonical_name(self._long(translation_id=4505262))

    def test_language_suffix_survives(self) -> None:
        """Главное для Jellyfin: без суффикса он не поймёт язык дорожки."""
        name = canonical_name(self._long(kind="sub", extension=".ass", lang="ru"))
        assert name.endswith(".ru.ass")

    def test_episode_title_is_dropped_whole(self) -> None:
        """Оборванное на полуслове название хуже, чем его отсутствие."""
        name = canonical_name(self._long(translation_id=4505262))
        assert "Ougi" not in name

    def test_audio_name_keeps_its_tail(self) -> None:
        name = audio_name(self._long(translation_id=4505262, extension=".aac"))
        assert len(name.encode("utf-8")) <= 250
        assert name.endswith(".ru.aac")
        assert "S02E05" in name

    def test_subtitles_keep_the_video_stem_and_language(self) -> None:
        """У субтитров жертвуем меткой, а не основой: на ней держится связь."""
        video = canonical_name(self._long(extension=".mp4", kind="voice"))
        stem = video[: -len(".ru.mp4")]

        name = subtitle_name_for_video(video, "Wakanim" * 8, "ru", ".ass", 999888)

        assert len(name.encode("utf-8")) <= 250
        assert name.startswith(stem), "основа имени видео обязана уцелеть"
        assert name.endswith(".ru.ass")


class TestTranslationIdInName:
    """Идентификатор перевода в имени файла.

    Файл, названный нами, должен уметь сказать, чей он: `t{id}` ведёт в каталоге
    к тайтлу и серии, и эта связь сильнее папки вокруг него. Раньше её несли
    только файлы, названные самим сайтом, — у пользователя таких 34 против 184
    наших.
    """

    def test_marker_carries_the_id(self) -> None:
        name = canonical_name(ctx(authors="Kazoku", translation_id=4505262))
        assert "[anime365_Kazoku_t4505262]" in name

    def test_without_an_id_the_name_is_unchanged(self) -> None:
        """Старые загрузки и тесты не должны переименоваться задним числом."""
        assert canonical_name(ctx(authors="Kazoku")) == canonical_name(
            ctx(authors="Kazoku", translation_id=None)
        )
        assert "[anime365_Kazoku]" in canonical_name(ctx(authors="Kazoku"))

    def test_a_long_author_name_does_not_eat_the_id(self) -> None:
        """Обрезка идёт по имени группы, номер приклеивается после неё."""
        name = canonical_name(ctx(authors="Ы" * 200, translation_id=4505262))
        assert "_t4505262]" in name

    def test_the_id_survives_the_neighbour_pattern(self) -> None:
        neighbour = "Zombie Land Saga S02E04 - Title [neoHEVC] Bluray-1080p.mkv"
        name = name_from_neighbour(neighbour, ctx(authors="Kazoku", translation_id=777))
        assert "[anime365_Kazoku_t777]" in name

    def test_subtitles_carry_their_own_id_after_the_video_one(self) -> None:
        """⚠️ Идентификаторов в имени два, и свой обязан быть последним.

        Основа имени наследуется у видеофайла целиком — иначе Jellyfin не
        подхватит субтитры, — поэтому чужой `t{id}` остаётся в строке. Правило
        разбора ищет последний; будь наш раньше, субтитры получили бы язык и
        вид перевода видеодорожки.
        """
        video = "Show S01E06 [anime365_Kazoku_t111] WEBDL-1080p.mp4"
        name = subtitle_name_for_video(video, "Wakanim", "ru", ".ass", 222)

        assert name == (
            "Show S01E06 [anime365_Kazoku_t111] WEBDL-1080p.anime365_Wakanim_t222.ru.ass"
        )
        assert name.rindex("t222") > name.rindex("t111")


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
class TestSubtitleMarkerIsNeverCutInHalf:
    """Номер перевода в имени субтитров либо целый, либо его нет вовсе.

    ⚠️ Здесь проверяется не длина и не язык — это уже закрыто соседним классом.
    Проверяется то, что он пропускал: метка субтитров лежит в **голове** имени,
    а укорачивалась она посимвольно, и в конце метки стоит `_t{id}`.

    Прогон по ста длинам имени видео дал шесть, где из `t4505262` получалось
    `t450526`, `t4505`, `t45`. Разбор читает такое как **годный** идентификатор
    и привязывает файл к чужому переводу — то есть отказ молчаливый, а цена
    ровно та, о которой предупреждает `author_marker`.
    """

    TID = 4505262

    @staticmethod
    def _video(length: int) -> str:
        return f"{'A' * length} S01E01 [Group] Bluray-1080p.mkv"

    def _name(self, length: int, authors: str = "Crunchyroll") -> str:
        return subtitle_name_for_video(
            self._video(length), authors, "ru", ".ass", self.TID
        )

    def test_no_length_yields_a_cut_identifier(self) -> None:
        rules = get_rules()
        cut: list[tuple[int, int]] = []
        for length in range(150, 250):
            name = self._name(length)
            assert len(name.encode("utf-8")) <= 250, length
            got = parse_filename(name, "series", rules).translation_id
            if got is not None and got != self.TID:
                cut.append((length, got))
        assert cut == [], f"номер обрезан и ведёт к чужому переводу: {cut}"

    def test_the_ladder_goes_down_in_whole_steps(self) -> None:
        # Проверка по существу: с ростом длины имени метка деградирует
        # ступенями — полная, без номера, с укороченной группой, никакой.
        assert self._name(185).endswith(".anime365_Crunchyroll_t4505262.ru.ass")
        assert self._name(190).endswith(".anime365_Crunchyroll.ru.ass")
        assert "anime365_" not in self._name(215)

    def test_a_short_name_is_untouched(self) -> None:
        # Обратная сторона: у обычного имени метка обязана остаться целиком.
        video = "Show S01E06 [anime365_Kazoku_t111] WEBDL-1080p.mp4"
        assert subtitle_name_for_video(video, "Wakanim", "ru", ".ass", 222) == (
            "Show S01E06 [anime365_Kazoku_t111] WEBDL-1080p.anime365_Wakanim_t222.ru.ass"
        )

    def test_language_and_extension_always_survive(self) -> None:
        for length in (150, 190, 215, 240):
            assert self._name(length).endswith(".ru.ass"), length

