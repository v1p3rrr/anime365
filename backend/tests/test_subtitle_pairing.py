"""Имя субтитров обязано совпадать с именем видео целиком.

Случай с сервера пользователя (2026-08-05), папка `Non Non Biyori`:

    Non Non Biyori S01E01 [anime365_Japanese_(BD)] WEBDL-1080p.ja.mp4
    Non Non Biyori S01E01 [anime365_Japanese_(BD)] WEBDL.ja.ass      ← не подхватывались

Видео получало метку с разрешением, субтитры — нет, и связь рвалась: ни
Jellyfin, ни плееры не показывают такие субтитры автоматически. Причина была в
`_source_tag()`, который у нулевой высоты отдавал голое `WEBDL` — «WEBDL-0p
выглядело нелепо». Эстетика оказалась дороже работоспособности.
"""

from __future__ import annotations

from app.services.naming import NamingContext, build_target_name, canonical_name


def ctx(kind: str, extension: str, height: int) -> NamingContext:
    return NamingContext(
        show_title="Non Non Biyori",
        season=1,
        episode=1,
        episode_title=None,
        authors="Japanese (BD)",
        height=height,
        lang="ja",
        kind=kind,
        extension=extension,
    )


class TestPairKeepsTheSameBasename:
    def test_video_and_subtitles_differ_only_by_extension(self) -> None:
        video = canonical_name(ctx("raw", ".mp4", 1080))
        subs = canonical_name(ctx("sub", ".ass", 1080))

        assert video.endswith(".mp4")
        assert subs.endswith(".ass")
        # Языковой суффикс у субтитров свой (`.ja`), у равки его нет вовсе —
        # сравниваем то, что до него: метку источника с разрешением.
        assert "WEBDL-1080p" in video
        assert "WEBDL-1080p" in subs, "без разрешения субтитры не подхватятся"

    def test_bare_tag_remains_when_height_is_unknown(self) -> None:
        """Разрешение известно не всегда — тогда метка остаётся короткой.

        Так называются звуковые дорожки фонотеки: у звука разрешения не бывает,
        и `WEBDL-0p` там был бы неправдой.
        """
        assert "WEBDL-0p" not in canonical_name(ctx("sub", ".ass", 0))


class TestExistingVideoStillWins:
    """Когда видео уже лежит рядом, basename берётся у него как есть.

    Это отдельная ветка, и она важнее общей схемы: у соседа может стоять чужая
    релиз-группа и метка `Bluray`, которую менять нельзя — иначе порвётся связь
    с файлом, который уже есть.
    """

    def test_subtitles_follow_the_local_video(self) -> None:
        existing = "Non Non Biyori S01E01 [neoHEVC] Bluray-1080p.mkv"
        name = build_target_name(ctx("sub", ".ass", 1080), existing_video=existing)

        assert name.startswith("Non Non Biyori S01E01 [neoHEVC] Bluray-1080p")
        assert name.endswith(".ass")
