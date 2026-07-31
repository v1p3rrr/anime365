"""Извлечение звуковой дорожки.

Часть тестов требует ffmpeg. В образе он есть, на машине разработки может не
быть — такие тесты пропускаются, но чистая логика (выбор дорожки, контейнер по
кодеку, нормализация языка) проверяется всегда.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from app.services.audio import (
    FALLBACK_CONTAINER,
    AudioStream,
    container_for_codec,
    extract_audio,
    ffmpeg_available,
    normalize_lang,
    pick_stream,
    probe_audio_streams,
)

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="в системе нет ffmpeg")


def _stream(index: int, lang: str | None, codec: str = "aac", channels: int = 2) -> AudioStream:
    return AudioStream(index=index, codec=codec, lang=lang, title=None, channels=channels)


class TestContainerByCodec:
    """Поток копируется без перекодирования, поэтому контейнер обязан его принять."""

    @pytest.mark.parametrize(
        ("codec", "expected"),
        [
            ("aac", ".m4a"),
            ("AAC", ".m4a"),
            ("flac", ".flac"),
            ("opus", ".opus"),
            ("ac3", ".ac3"),
            ("mp3", ".mp3"),
            ("vorbis", ".ogg"),
        ],
    )
    def test_known_codecs(self, codec: str, expected: str) -> None:
        assert container_for_codec(codec) == expected

    def test_unknown_codec_falls_back_to_matroska(self) -> None:
        # Matroska принимает практически любой звук — это единственный контейнер,
        # с которым `-c:a copy` не упрётся в неподдерживаемый кодек.
        assert container_for_codec("что-то новое") == FALLBACK_CONTAINER
        assert container_for_codec("") == FALLBACK_CONTAINER


class TestLangNormalization:
    def test_iso_639_2_maps_to_our_codes(self) -> None:
        # В дорожках язык пишут трёхбуквенным кодом, у нас двухбуквенные.
        assert normalize_lang("jpn") == "ja"
        assert normalize_lang("rus") == "ru"
        assert normalize_lang("eng") == "en"

    def test_case_and_spaces_ignored(self) -> None:
        assert normalize_lang("  RUS ") == "ru"

    def test_language_beyond_the_usual_three_is_recognised(self) -> None:
        # Раньше ожидалось None: приведение знало только ru/en/ja. Немецкая
        # дорожка теперь распознаётся, и оба её трёхбуквенных кода дают один
        # результат — ради этого и заведён ISO-справочник.
        assert normalize_lang("ger") == "de"
        assert normalize_lang("deu") == "de"

    def test_absent_language_is_none(self) -> None:
        assert normalize_lang(None) is None
        assert normalize_lang("und") is None


class TestStreamChoice:
    def test_single_stream_wins_regardless_of_language(self) -> None:
        # Дорожка одна — выбирать нечего, даже если язык не совпал.
        only = _stream(1, None)
        assert pick_stream([only], "ru") is only

    def test_language_decides_when_several(self) -> None:
        streams = [_stream(1, "ja"), _stream(2, "ru")]
        assert pick_stream(streams, "ru").index == 2
        assert pick_stream(streams, "ja").index == 1

    def test_most_channels_wins_within_language(self) -> None:
        # У одного языка бывает основная дорожка и моно-комментарии.
        streams = [_stream(1, "ru", channels=1), _stream(2, "ru", channels=6)]
        assert pick_stream(streams, "ru").index == 2

    def test_falls_back_to_first_when_language_absent(self) -> None:
        streams = [_stream(1, "ja"), _stream(2, "en")]
        # Не ошибка: лучше отдать что-то, чем ничего, но вызывающий это логирует.
        assert pick_stream(streams, "ru").index == 1

    def test_no_streams_gives_none(self) -> None:
        assert pick_stream([], "ru") is None


@needs_ffmpeg
class TestRealExtraction:
    @staticmethod
    def _make_sample(path: Path) -> Path:
        """Видео с двумя звуковыми дорожками: японской и русской."""
        source = path / "sample.mkv"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
                "-map", "0:v", "-map", "1:a", "-map", "2:a",
                "-metadata:s:a:0", "language=jpn",
                "-metadata:s:a:1", "language=rus",
                "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source),
            ],
            check=True,
            capture_output=True,
        )
        return source

    def test_probe_reads_languages_and_indexes(self, tmp_path: Path) -> None:
        source = self._make_sample(tmp_path)
        streams = asyncio.run(probe_audio_streams(source))

        assert [s.lang for s in streams] == ["ja", "ru"]
        # Индекс — это позиция в файле целиком, а не номер среди звуковых:
        # нулевой занят видео, поэтому дорожки начинаются с единицы.
        assert [s.index for s in streams] == [1, 2]

    def test_extracts_requested_language_without_reencoding(self, tmp_path: Path) -> None:
        source = self._make_sample(tmp_path)
        streams = asyncio.run(probe_audio_streams(source))
        russian = pick_stream(streams, "ru")

        target = tmp_path / "out.m4a"
        asyncio.run(extract_audio(source, russian, target))

        assert target.stat().st_size > 0
        result = asyncio.run(probe_audio_streams(target))
        assert len(result) == 1
        # Кодек обязан остаться прежним: перекодирование испортило бы качество.
        assert result[0].codec == "aac"

    def test_broken_source_reports_error(self, tmp_path: Path) -> None:
        from app.services.audio import AudioError

        broken = tmp_path / "broken.mkv"
        broken.write_bytes(b"not a video file at all")

        with pytest.raises(AudioError):
            asyncio.run(probe_audio_streams(broken))
