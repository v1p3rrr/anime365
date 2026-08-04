"""Вид перевода `subv` — надписи и песни, а не полный перевод.

Найдено пользователем: в сводке переводов Yuru Yuri вместо подписи печатался
сам ключ словаря — «Русский kind.subv1 авторов». Разбор на живом API показал,
что это отдельный вид, и обходиться с ним надо не так, как с обычными
субтитрами (подробности — `claude/02-api-findings.md`).
"""

from __future__ import annotations

from app.services.naming import NamingContext, build_target_name
from app.services.translation_scan import counts_from_translations
from app.services.translation_types import SIGNS_AND_SONGS, is_subtitle_kind


class TestSubtitleKinds:
    def test_subv_is_a_subtitle_kind(self) -> None:
        """По поведению это файл субтитров: у него есть `subtitlesUrl` с `.ass`."""
        assert is_subtitle_kind("subv")
        assert is_subtitle_kind("sub")

    def test_video_kinds_are_not(self) -> None:
        assert not is_subtitle_kind("voice")
        assert not is_subtitle_kind("raw")
        assert not is_subtitle_kind("")
        assert not is_subtitle_kind(None)


class TestNaming:
    """Языковой суффикс — главное практическое следствие."""

    @staticmethod
    def _ctx(kind: str) -> NamingContext:
        return NamingContext(
            show_title="Yuru Yuri",
            season=1,
            episode=1,
            episode_title="",
            authors="Yousei-raws",
            kind=kind,
            lang="ru",
            height=0,
            extension=".ass",
        )

    def test_subv_keeps_the_language_suffix(self) -> None:
        """Без этого файл ложился без `.ru`, и Jellyfin не понимал его язык."""
        name = build_target_name(self._ctx(SIGNS_AND_SONGS))
        assert ".ru.ass" in name, name

    def test_plain_sub_unchanged(self) -> None:
        assert ".ru.ass" in build_target_name(self._ctx("sub"))

    def test_subv_next_to_existing_video_matches_its_basename(self) -> None:
        """Субтитры обязаны совпасть с именем видео, иначе Jellyfin их не свяжет."""
        video = "Yuru Yuri S01E01 - Middle School Debut! [Doki] Bluray-1080p.mkv"
        name = build_target_name(self._ctx(SIGNS_AND_SONGS), existing_video=video)
        assert name.startswith("Yuru Yuri S01E01 - Middle School Debut! [Doki] Bluray-1080p.")
        assert name.endswith(".ru.ass")


class TestCatalogCounters:
    def test_subv_does_not_count_as_subtitles(self) -> None:
        """Фильтр «есть русские субтитры» обязан отвечать про перевод речи.

        В `subv` реплик нет вовсе — только опенинг, эндинг и вывески. Засчитав
        его, фильтр привёл бы к тайтлу, где речь никто не переводил.
        """
        counts = counts_from_translations(
            [
                {"typeKind": "subv", "typeLang": "ru"},
                {"typeKind": "sub", "typeLang": "ru"},
            ]
        )
        assert counts["sub_ru"] == 1
        # Но в общем числе он есть: «переводов 2, а по типам 1» — не потеря.
        assert counts["total"] == 2
