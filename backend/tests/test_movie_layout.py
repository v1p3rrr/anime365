"""Раскладка фильмов и связок фильмов.

В каталоге есть тайтлы типа `movie` с несколькими эпизодами — это связка
отдельных фильмов, а не один. У фильма в имени файла нет номера серии, поэтому
без разделения обе части получали одно имя и вторая ложилась рядом как «v2» —
то есть выглядела дублем первой. Тесты закрепляют оба конца этой ошибки.
"""

from __future__ import annotations

from app.services.coverage import auto_is_movie, is_multipart_movie, resolve_is_movie
from app.services.naming import NamingContext, canonical_name, folder_name_for_title


def _movie_name(episode: float) -> str:
    return canonical_name(
        NamingContext(
            show_title="Girls Band Cry Movie",
            season=1,
            episode=episode,
            episode_title="",
            authors="Someone",
            lang="ru",
            kind="voice",
            height=1080,
            extension=".mp4",
            year=2025,
            is_movie=True,
        )
    )


class TestAutoLayout:
    def test_single_movie_is_a_movie(self):
        assert auto_is_movie("movie", 1) is True

    def test_collection_of_movies_is_filed_as_movies(self):
        # Решение изменено по возражению пользователя: части многосерийного
        # фильма — самостоятельные фильмы (отдельный прокат, отдельные страницы
        # на AniList), и место им в киношной библиотеке. Раньше здесь ожидался
        # False, и связку уводили в сериалы из-за коллизии имён — теперь она
        # решается именем ПАПКИ, а не подменой типа.
        assert auto_is_movie("movie", 2) is True

    def test_series_is_never_a_movie(self):
        assert auto_is_movie("tv", 12) is False
        assert auto_is_movie("ova", 1) is False

    def test_title_without_episodes_yet_is_still_a_movie(self):
        # У новых фильмов эпизодов в API может не быть вовсе.
        assert auto_is_movie("movie", 0) is True


class TestUserDecisionWins:
    def test_manual_movie_overrides_auto(self):
        assert resolve_is_movie(True, "tv", 12) is True

    def test_manual_series_overrides_auto(self):
        assert resolve_is_movie(False, "movie", 1) is False

    def test_none_falls_back_to_auto(self):
        assert resolve_is_movie(None, "movie", 3) is True
        assert resolve_is_movie(None, "tv", 12) is False


class TestPartsDoNotCollide:
    def test_forced_movie_layout_keeps_parts_apart(self):
        # Даже если связку назначили фильмом вручную, имена обязаны различаться:
        # иначе вторая часть сохранится как «v2» и будет выглядеть дублем.
        assert _movie_name(1) != _movie_name(2)

    def test_ordinary_movie_name_has_no_part_number(self):
        # У обычного фильма номер в имени не нужен и не должен появляться.
        assert " - 1 - " not in _movie_name(1)
        assert _movie_name(1).startswith("Girls Band Cry Movie (2025) - [")


class TestFolderNameFromTruncatedEnglish:
    def test_romaji_wins_when_english_is_a_fragment(self):
        # У 27995 английское название — «Morfonication», без франшизы. Папка с
        # огрызком не встанет рядом с остальными частями.
        titles = {"en": "Morfonication", "romaji": "BanG Dream! Morfonication"}
        assert folder_name_for_title(titles) == "BanG Dream! Morfonication"

    def test_english_kept_when_spellings_differ_in_substance(self):
        titles = {"en": "Zombie Land Saga", "romaji": "Zombieland Saga"}
        assert folder_name_for_title(titles) == "Zombie Land Saga"

    def test_english_kept_when_it_is_the_longer_one(self):
        titles = {"en": "YuruYuri Happy Go Lily", "romaji": "Yuru Yuri"}
        assert folder_name_for_title(titles) == "YuruYuri Happy Go Lily"


class TestMultipartFolders:
    """У каждой части своя папка — иначе Jellyfin сочтёт вторую альтернативной
    версией первой, а не отдельным фильмом."""

    def test_single_movie_has_no_part_number(self):
        # Номер у одиночного фильма мешал бы Jellyfin сопоставить папку с TMDB,
        # а без этого не соберётся и коллекция, которой он связывает части.
        assert is_multipart_movie("movie", 1) is False
        assert is_multipart_movie("movie", 0) is False

    def test_collection_is_multipart(self):
        assert is_multipart_movie("movie", 2) is True

    def test_series_is_never_multipart(self):
        assert is_multipart_movie("tv", 12) is False

    def test_folder_names_of_parts_differ(self):
        titles = {"romaji": "Girls Band Cry Movie"}
        first = folder_name_for_title(titles, year=2025, is_movie=True, part=1)
        second = folder_name_for_title(titles, year=2025, is_movie=True, part=2)
        assert first != second
        assert first.endswith(" - 1")
        assert second.endswith(" - 2")

    def test_single_movie_folder_keeps_the_clean_name(self):
        # Имя без хвоста — именно то, что Jellyfin сопоставляет с TMDB.
        assert (
            folder_name_for_title({"romaji": "Your Name"}, year=2016, is_movie=True)
            == "Your Name (2016)"
        )
