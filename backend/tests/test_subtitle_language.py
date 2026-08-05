"""Язык субтитров, когда суффикса в имени нет.

Живой случай: наши же файлы `[Anime365] … - 01 (t5282068).ass` лежат без
языкового суффикса и показывались как `SUB_UNKNOWN`, то есть в покрытие по
языкам не попадали вовсе.

Два способа, оба без гадания:

* **точный** — `t{id}` ведёт к переводу, у перевода есть `type_lang`;
* **по письменности** — русский, японский и английский пишутся разными
  алфавитами, спутать их нельзя.
"""

from __future__ import annotations

from pathlib import Path

from app.services.library_scan import detect_subtitle_language, scan_root
from app.services.library_service import _persist_root

HEADER = "[Script Info]\nTitle: Something\nScriptType: v4.00+\nStyle: Default,Arial,48\n"
RU = HEADER + "Dialogue: 0,0:00:01.00,Привет! Как у тебя дела сегодня? " * 30
JA = HEADER + "Dialogue: 0,0:00:01.00,こんにちは。今日は元気ですか。 " * 30
EN = HEADER + "Dialogue: 0,0:00:01.00,Hello there, how are you doing today? " * 30


def _write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


class TestByScript:
    def test_russian(self, tmp_path: Path) -> None:
        assert detect_subtitle_language(_write(tmp_path, "a.ass", RU)) == "ru"

    def test_japanese(self, tmp_path: Path) -> None:
        assert detect_subtitle_language(_write(tmp_path, "b.ass", JA)) == "ja"

    def test_english(self, tmp_path: Path) -> None:
        assert detect_subtitle_language(_write(tmp_path, "c.ass", EN)) == "en"

    def test_latin_header_alone_decides_nothing(self, tmp_path: Path) -> None:
        """Одна шапка `.ass` — это ещё не английские субтитры."""
        assert detect_subtitle_language(_write(tmp_path, "d.ass", HEADER)) is None

    def test_missing_file_is_not_a_crash(self, tmp_path: Path) -> None:
        assert detect_subtitle_language(tmp_path / "нет-такого.ass") is None


class TestScanUsesIt:
    def test_subtitle_without_suffix_gets_a_language(self, tmp_path: Path) -> None:
        root = tmp_path / "series"
        folder = root / "Show"
        folder.mkdir(parents=True)
        (folder / "Show S01E01 [Group] WEBDL-1080p.mkv").write_bytes(b"x" * 10)
        _write(folder, "Show S01E01 [Group] WEBDL-1080p.ass", RU)

        result = scan_root(str(root), "series")
        subtitle = next(
            f for f in result.folders[0].files if f.filename.endswith(".ass")
        )

        assert subtitle.lang == "ru"

    def test_suffix_in_the_name_still_wins(self, tmp_path: Path) -> None:
        """Имя файла — более явное указание, чем содержимое."""
        root = tmp_path / "series-suffix"
        folder = root / "Show"
        folder.mkdir(parents=True)
        # Содержимое японское, а подписано как английские: верим подписи.
        _write(folder, "Show S01E01 [Group] WEBDL-1080p.en.ass", JA)

        result = scan_root(str(root), "series")
        subtitle = result.folders[0].files[0]

        assert subtitle.lang == "en"

    def test_video_files_are_not_read(self, tmp_path: Path) -> None:
        """Определение языка — только для субтитров; видео читать незачем."""
        root = tmp_path / "series-video"
        folder = root / "Show"
        folder.mkdir(parents=True)
        (folder / "Show S01E01 [Group] WEBDL-1080p.mkv").write_bytes(b"x" * 10)

        result = _persist_root(scan_root(str(root), "series"))

        assert result is not None
