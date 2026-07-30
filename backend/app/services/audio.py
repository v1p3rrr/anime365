"""Извлечение звуковой дорожки из видео.

Дорожка вырезается **копированием потока** (`-c:a copy`): перекодирование
испортило бы качество и заняло процессор, а нам нужен ровно тот звук, который
уже есть в файле.

Из этого следует главное ограничение: контейнер обязан принимать этот кодек.
Поэтому расширение выбирается по кодеку, а не берётся одно на все случаи.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class AudioError(RuntimeError):
    """Не удалось разобрать или извлечь дорожку."""


# Кодек → расширение. Копирование потока требует контейнера, который этот кодек
# принимает: AAC не положить в .flac, а Opus не положить в .m4a без ремукса.
# Всё неизвестное уходит в .mka — Matroska принимает практически любой звук.
CONTAINER_BY_CODEC: dict[str, str] = {
    "aac": ".m4a",
    "alac": ".m4a",
    "ac3": ".ac3",
    "eac3": ".eac3",
    "dts": ".dts",
    "flac": ".flac",
    "opus": ".opus",
    "vorbis": ".ogg",
    "mp3": ".mp3",
    "mp2": ".mp2",
    "wmav2": ".wma",
    "pcm_s16le": ".wav",
    "pcm_s24le": ".wav",
    "truehd": ".thd",
}

FALLBACK_CONTAINER = ".mka"

# ISO 639-2/B и прочие написания, которые встречаются в дорожках, к нашим кодам.
LANG_ALIASES: dict[str, str] = {
    "rus": "ru",
    "ru": "ru",
    "jpn": "ja",
    "jap": "ja",
    "ja": "ja",
    "eng": "en",
    "en": "en",
}


def container_for_codec(codec: str) -> str:
    """Расширение под кодек. Неизвестное — в Matroska, она принимает всё."""
    return CONTAINER_BY_CODEC.get((codec or "").lower(), FALLBACK_CONTAINER)


def normalize_lang(raw: str | None) -> str | None:
    if not raw:
        return None
    return LANG_ALIASES.get(raw.strip().lower())


@dataclass(frozen=True)
class AudioStream:
    """Одна звуковая дорожка внутри видеофайла."""

    index: int
    codec: str
    lang: str | None
    title: str | None
    channels: int

    @property
    def extension(self) -> str:
        return container_for_codec(self.codec)


def ffmpeg_available() -> bool:
    """Есть ли ffmpeg и ffprobe. Без них фонотека не работает вовсе."""
    return bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))


async def _run(*args: str) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return process.returncode or 0, stdout, stderr


async def probe_audio_streams(source: Path) -> list[AudioStream]:
    """Список звуковых дорожек файла.

    Берём именно ffprobe, а не `<streamdetails>` из `.nfo`: там нет индекса
    дорожки, а для извлечения нужен именно он. Плюс `.nfo` пишет Jellyfin, и
    его данные бывают неверными — у одного файла там стоял язык `ita` у видео.
    """
    if not ffmpeg_available():
        raise AudioError("В образе нет ffmpeg — извлечение звука недоступно")

    code, stdout, stderr = await _run(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index,codec_name,channels:stream_tags=language,title",
        "-of",
        "json",
        str(source),
    )
    if code != 0:
        raise AudioError(f"ffprobe не смог прочитать файл: {stderr.decode(errors='replace')[:300]}")

    try:
        payload = json.loads(stdout or b"{}")
    except json.JSONDecodeError as exc:
        raise AudioError(f"Не разобрать ответ ffprobe: {exc}") from exc

    streams: list[AudioStream] = []
    for item in payload.get("streams") or []:
        tags = item.get("tags") or {}
        streams.append(
            AudioStream(
                index=int(item["index"]),
                codec=item.get("codec_name") or "",
                lang=normalize_lang(tags.get("language")),
                title=tags.get("title"),
                channels=int(item.get("channels") or 0),
            )
        )
    return streams


def pick_stream(streams: list[AudioStream], preferred_lang: str | None) -> AudioStream | None:
    """Какую дорожку извлекать.

    Обычно она одна — тогда выбирать нечего и язык не важен. Если дорожек
    несколько, берём по языку перевода; при отсутствии совпадения — первую,
    но это уже повод сообщить пользователю, что язык не совпал.
    """
    if not streams:
        return None
    if len(streams) == 1:
        return streams[0]

    if preferred_lang:
        matching = [s for s in streams if s.lang == preferred_lang]
        if matching:
            # При нескольких дорожках одного языка берём самую многоканальную:
            # обычно это основная, а моно-дорожка рядом — комментарии.
            return max(matching, key=lambda s: s.channels)

    return streams[0]


async def extract_audio(source: Path, stream: AudioStream, target: Path) -> None:
    """Вырезает дорожку в отдельный файл без перекодирования.

    `-map 0:{index}` берёт конкретную дорожку по её индексу в файле, а не
    «первую звуковую»: при нескольких дорожках это разные вещи.
    """
    if not ffmpeg_available():
        raise AudioError("В образе нет ffmpeg — извлечение звука недоступно")

    target.parent.mkdir(parents=True, exist_ok=True)
    code, _, stderr = await _run(
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        # Перезаписываем: повторный запуск после сбоя не должен упираться
        # в вопрос «файл существует», на который некому ответить.
        "-y",
        "-i",
        str(source),
        "-map",
        f"0:{stream.index}",
        "-vn",
        "-c:a",
        "copy",
        str(target),
    )
    if code != 0:
        target.unlink(missing_ok=True)
        raise AudioError(f"ffmpeg не смог извлечь дорожку: {stderr.decode(errors='replace')[:300]}")

    if not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise AudioError("ffmpeg отработал без ошибки, но файл пустой")
