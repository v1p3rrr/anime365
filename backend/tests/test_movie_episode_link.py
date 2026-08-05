"""Файлы фильма должны привязываться к серии.

Реальный случай из библиотеки: в папке «Chainsaw Man - The Movie - Reze Arc»
лежит видео и три файла субтитров, записи о них в базе есть, а на странице
тайтла нет ни одного. Причина: связь ставится только по номеру серии, а у имён
вида «… Reze Arc (2025) - [WEBDL-1080p] [h265] [EAC3 5.1] [JA].mkv» номера нет
и взяться ему неоткуда.

Имена файлов здесь настоящие, скопированы с сервера.
"""

from __future__ import annotations

from pathlib import Path

from app.services.filename_rules import get_rules
from app.services.library_scan import scan_folder

MOVIE = "Chainsaw Man - The Movie - Reze Arc (2025) - [WEBDL-1080p] [h265] [EAC3 5.1] [JA]"


def _folder(root: Path, name: str, filenames: list[str]) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    for filename in filenames:
        (folder / filename).write_bytes(b"x" * 10)
    return folder


def _by_name(result, filename: str):
    return next(f for f in result.files if f.filename == filename)


class TestMovieFolder:
    def test_single_video_becomes_episode_one(self, tmp_path: Path) -> None:
        folder = _folder(tmp_path, "Reze Arc", [f"{MOVIE}.mkv"])

        result = scan_folder(folder, tmp_path, "movie", get_rules())

        assert _by_name(result, f"{MOVIE}.mkv").episode == 1.0

    def test_sidecar_subtitle_follows_its_video(self, tmp_path: Path) -> None:
        """Тот самый японский саб, которого не было видно на странице."""
        folder = _folder(tmp_path, "Reze Arc", [f"{MOVIE}.mkv", f"{MOVIE}.ja.srt"])

        result = scan_folder(folder, tmp_path, "movie", get_rules())

        subtitle = _by_name(result, f"{MOVIE}.ja.srt")
        assert subtitle.episode == 1.0
        assert subtitle.lang == "ja"

    def test_downloaded_subtitles_keep_their_own_number(self, tmp_path: Path) -> None:
        """У скачанных нами файлов номер в имени есть — его и оставляем."""
        ours = "Chainsaw Man Movie Reze-hen S01E01 [anime365_Crunchyroll] WEBDL.ru.ass"
        folder = _folder(tmp_path, "Reze Arc", [f"{MOVIE}.mkv", ours])

        result = scan_folder(folder, tmp_path, "movie", get_rules())

        assert _by_name(result, ours).episode == 1.0
        assert _by_name(result, ours).matched_rule_id != "sidecar_of_video"

    def test_multipart_movie_is_left_alone(self, tmp_path: Path) -> None:
        """Двум частям одну единицу не приписываем — это склеило бы их в одну серию."""
        folder = _folder(
            tmp_path,
            "Cencoroll Connect",
            ["Cencoroll (2009) - [WEBDL-1080p].mkv", "Connected (2020) - [WEBDL-1080p].mkv"],
        )

        result = scan_folder(folder, tmp_path, "movie", get_rules())

        assert [f.episode for f in result.files] == [None, None]

    def test_series_root_does_not_get_a_free_episode(self, tmp_path: Path) -> None:
        """Правило про единственное видео — только для корня фильмов."""
        folder = _folder(tmp_path, "Some Show", ["Some Show - unparsable name.mkv"])

        result = scan_folder(folder, tmp_path, "series", get_rules())

        assert result.files[0].episode is None


class TestSidecarPairing:
    def test_series_subtitle_without_number_follows_video(self, tmp_path: Path) -> None:
        video = "Chainsaw Man S01E05 - Gun Devil [Group] WEBDL-1080p.mkv"
        folder = _folder(tmp_path, "Chainsaw Man", [video, f"{Path(video).stem}.ru.ass"])

        result = scan_folder(folder, tmp_path, "series", get_rules())

        assert _by_name(result, f"{Path(video).stem}.ru.ass").episode == 5.0

    def test_longest_matching_video_wins(self, tmp_path: Path) -> None:
        """«Part 1» не должен перехватывать субтитры «Part 11»."""
        short = "Show - Part 1 - 01 (WEBDL).mkv"
        long = "Show - Part 1 - 01 (WEBDL) Extended - 11 (WEBDL).mkv"
        folder = _folder(tmp_path, "Show", [short, long, f"{Path(long).stem}.ru.ass"])

        result = scan_folder(folder, tmp_path, "series", get_rules())

        subtitle = _by_name(result, f"{Path(long).stem}.ru.ass")
        assert subtitle.episode == _by_name(result, long).episode

    def test_unrelated_subtitle_stays_unmatched(self, tmp_path: Path) -> None:
        folder = _folder(
            tmp_path,
            "Show",
            ["Show S01E01 [Group] WEBDL-1080p.mkv", "Notes about the release.ru.ass"],
        )

        result = scan_folder(folder, tmp_path, "series", get_rules())

        assert _by_name(result, "Notes about the release.ru.ass").episode is None
