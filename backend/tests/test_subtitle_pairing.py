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


class TestMovieSubtitlesFindTheVideo:
    """Случай с сервера: фильм «Chainsaw Man - The Movie - Reze Arc».

    Субтитры легли под каноническим именем сериала и не подхватились:

        Chainsaw Man - The Movie - Reze Arc (2025) - [WEBDL-1080p] [h265]….mkv
        Chainsaw Man Movie Reze-hen S01E01 [anime365_Crunchyroll]….ass  ← мимо

    Причина: соседнее видео искалось строго по номеру серии, а у файла фильма
    номера нет вовсе. У сериала (Akudama Drive) та же логика срабатывала, и
    беда выглядела выборочной.
    """

    def _folder_with(self, filenames: list[tuple[str, float | None]]) -> int:
        import uuid

        from app.core.db import session_scope
        from app.models.library import LibraryFile, LibraryFolder, LibraryRoot

        # Путь корня уникален, а база тестов переживает прогоны — поэтому
        # каждому случаю свой путь, иначе второй тест падает на вставке.
        unique = uuid.uuid4().hex[:8]
        with session_scope() as session:
            root = LibraryRoot(path=f"/tmp/movies-{unique}", kind="movie", is_available=True)
            session.add(root)
            session.flush()
            folder = LibraryFolder(root_id=root.id, rel_path=f"Reze Arc {unique}", name="Reze Arc")
            session.add(folder)
            session.flush()
            for name, episode in filenames:
                session.add(
                    LibraryFile(
                        folder_id=folder.id, filename=name, kind="video", episode=episode
                    )
                )
            session.flush()
            return folder.id

    def test_single_video_without_episode_number_is_used(self) -> None:
        from app.services.download_queue import _existing_video_for_episode

        movie = "Chainsaw Man - The Movie - Reze Arc (2025) - [WEBDL-1080p] [h265].mkv"
        folder_id = self._folder_with([(movie, None)])

        assert _existing_video_for_episode(folder_id, 1) == movie

    def test_several_unnumbered_videos_stay_ambiguous(self) -> None:
        """Гадать нельзя: субтитры приклеились бы к чужой серии."""
        from app.services.download_queue import _existing_video_for_episode

        folder_id = self._folder_with([("part one.mkv", None), ("part two.mkv", None)])

        assert _existing_video_for_episode(folder_id, 1) is None

    def test_episode_number_still_wins(self) -> None:
        from app.services.download_queue import _existing_video_for_episode

        folder_id = self._folder_with([("Show S01E01.mkv", 1), ("Show S01E02.mkv", 2)])

        assert _existing_video_for_episode(folder_id, 2) == "Show S01E02.mkv"
