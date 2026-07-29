"""Разбор .nfo, которые Jellyfin кладёт рядом с медиафайлами.

Это самый надёжный источник данных о локальной библиотеке: там уже есть названия,
номера сезона и серии, год, дата премьеры и список дорожек. Парсинг имён файлов —
запасной путь для папок, где .nfo нет.

⚠️ Данным нельзя доверять слепо:
  * <shikimoriid> встречается неверный (у YuruYuri указывает на другое аниме);
  * в <streamdetails> у видеодорожки попадается чужой язык (у Fate стоит "ita").
Поэтому ID проверяются по названию, а дорожки считаются подсказкой.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

log = logging.getLogger(__name__)


@dataclass
class StreamInfo:
    """Дорожки внутри контейнера — из <fileinfo><streamdetails>."""

    subtitle_languages: list[str] = field(default_factory=list)
    audio_languages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtitle_languages": self.subtitle_languages,
            "audio_languages": self.audio_languages,
        }


@dataclass
class NfoData:
    kind: str  # tvshow | season | episode | movie
    path: str

    title: str | None = None
    original_title: str | None = None
    show_title: str | None = None
    year: int | None = None
    premiered: str | None = None

    season: int | None = None
    episode: int | None = None

    # Внешние ID. anilist_id — единственный, совпавший везде на проверке.
    anilist_id: int | None = None
    shikimori_id: int | None = None
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    kitsu_id: int | None = None

    plot: str | None = None
    streams: StreamInfo = field(default_factory=StreamInfo)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "title": self.title,
            "original_title": self.original_title,
            "show_title": self.show_title,
            "year": self.year,
            "premiered": self.premiered,
            "season": self.season,
            "episode": self.episode,
            "anilist_id": self.anilist_id,
            "shikimori_id": self.shikimori_id,
            "tvdb_id": self.tvdb_id,
            "tmdb_id": self.tmdb_id,
            "imdb_id": self.imdb_id,
            "kitsu_id": self.kitsu_id,
            "streams": self.streams.to_dict(),
        }

    @property
    def season_quarter(self) -> tuple[int, int] | None:
        """(год, месяц) премьеры — по ним сезон сопоставляется с полем каталога.

        Именно это разводит сезоны: «2012-07-02» ложится на «Лето 2012», тогда как
        одного года не хватает (у YuruYuri в 2015-м три разных тайтла).
        """
        if not self.premiered or len(self.premiered) < 7:
            return None
        try:
            return int(self.premiered[0:4]), int(self.premiered[5:7])
        except ValueError:
            return None


_ROOT_TO_KIND = {
    "tvshow": "tvshow",
    "season": "season",
    "episodedetails": "episode",
    "movie": "movie",
}


def _text(root: ElementTree.Element, tag: str) -> str | None:
    node = root.find(tag)
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def _int(root: ElementTree.Element, tag: str) -> int | None:
    raw = _text(root, tag)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    # Jellyfin пишет -1 в <season>/<episode> у tvshow.nfo — это «не задано».
    return value if value >= 0 else None


def _first_int(root: ElementTree.Element, tags: tuple[str, ...]) -> int | None:
    for tag in tags:
        value = _int(root, tag)
        if value is not None:
            return value
    return None


def _parse_streams(root: ElementTree.Element) -> StreamInfo:
    info = StreamInfo()
    details = root.find("fileinfo/streamdetails")
    if details is None:
        return info

    for node in details.findall("subtitle"):
        lang = (node.findtext("language") or "").strip().lower()
        if lang and lang not in info.subtitle_languages:
            info.subtitle_languages.append(lang)

    for node in details.findall("audio"):
        lang = (node.findtext("language") or "").strip().lower()
        if lang and lang not in info.audio_languages:
            info.audio_languages.append(lang)

    return info


def parse_nfo(path: Path) -> NfoData | None:
    """Читает один .nfo. Возвращает None, если файл не разобрался."""
    try:
        # Jellyfin иногда пишет мусор перед XML-декларацией, поэтому читаем текстом.
        raw = path.read_text(encoding="utf-8", errors="replace").lstrip("﻿ \n\r\t")
        start = raw.find("<?xml")
        if start > 0:
            raw = raw[start:]
        root = ElementTree.fromstring(raw)
    except (OSError, ElementTree.ParseError) as exc:
        log.warning("Не удалось разобрать %s: %s", path, exc)
        return None

    kind = _ROOT_TO_KIND.get(root.tag)
    if kind is None:
        log.debug("Неизвестный корневой тег %s в %s", root.tag, path)
        return None

    data = NfoData(kind=kind, path=str(path))
    data.title = _text(root, "title")
    data.original_title = _text(root, "originaltitle")
    data.show_title = _text(root, "showtitle")
    data.year = _int(root, "year")
    data.premiered = _text(root, "premiered") or _text(root, "releasedate") or _text(root, "aired")
    data.plot = _text(root, "plot")

    data.season = _first_int(root, ("seasonnumber", "season"))
    data.episode = _int(root, "episode")

    data.anilist_id = _int(root, "anilistid")
    data.shikimori_id = _int(root, "shikimoriid")
    data.tvdb_id = _int(root, "tvdbid")
    data.tmdb_id = _int(root, "tmdbid")
    data.kitsu_id = _int(root, "kitsuid")
    # Тег называется по-разному в tvshow.nfo и movie.nfo.
    data.imdb_id = _text(root, "imdb_id") or _text(root, "imdbid")

    data.streams = _parse_streams(root)
    return data


def candidate_titles(nfo: NfoData | None, folder_name: str) -> list[str]:
    """Строки, по которым имеет смысл искать тайтл в каталоге.

    Порядок важен: название из .nfo обычно точнее имени папки. У YuruYuri папка
    называется «YuruYuri - Happy Go Lily», а в tvshow.nfo стоит «Yuru Yuri» —
    ровно romaji-название из каталога.
    """
    out: list[str] = []

    def add(value: str | None) -> None:
        if value and value.strip() and value not in out:
            out.append(value.strip())

    if nfo is not None:
        add(nfo.title)
        add(nfo.original_title)
        add(nfo.show_title)

    add(folder_name)
    return out
