"""Построение имён файлов для скачанного.

Правила зафиксированы пользователем и подробно описаны в claude/06-naming-rules.md.
Коротко, два сценария и они РАЗНЫЕ:

  1. Скачали видео → имя строится по образцу соседних файлов, релиз-группа
     заменяется на `anime365_{Автор}`, метка источника ВСЕГДА `WEBDL`.

  2. Скачали только субтитры к существующему видео → имя обязано совпадать с
     basename видеофайла, иначе Jellyfin их не подхватит. Здесь basename берётся
     КАК ЕСТЬ, вместе с чужой релиз-группой и чужим `Bluray`. Правило про WEBDL
     применяется только к именам, которые создаём мы сами.

⚠️ `authorsSummary` приходит с внешнего сайта и попадает прямо в имя файла —
это недоверенный ввод, санитизация обязательна.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.services.translation_types import is_subtitle_kind

log = logging.getLogger(__name__)

# Всё, что скачано с Anime365, помечается WEBDL, даже если соседние эпизоды
# помечены Bluray или HDTV. Решение пользователя: врать в имени файла не будем.
SOURCE_TAG = "WEBDL"

# Языковой суффикс. Японскому оригиналу суффикс не добавляется вовсе.
VIDEO_LANG_SUFFIX = {"ru": ".ru", "en": ".en"}
SUB_LANG_SUFFIX = {"ru": ".ru", "en": ".en", "ja": ".ja"}

# Запрещённые в именах символы. Двоеточие и слеш встречаются в названиях аниме
# постоянно, а `[` и `]` ломают последующий разбор релиз-группы.
_FORBIDDEN = re.compile(r'[/\\:*?"<>|\[\]\x00-\x1f]')
_SPACES = re.compile(r"\s+")

# Зарезервированные имена Windows: библиотека может лежать на SMB-шаре.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Предел длины имени файла в ext4 — 255 БАЙТ, а не символов. Кириллица и
# японский в UTF-8 многобайтовые, поэтому считаем именно байты.
MAX_FILENAME_BYTES = 250
MAX_AUTHOR_CHARS = 60


def sanitize_component(value: str, max_chars: int = 120) -> str:
    """Приводит произвольную строку к безопасному куску имени файла."""
    if not value:
        return ""

    text = unicodedata.normalize("NFC", value)
    text = _FORBIDDEN.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    # Windows и SMB не любят точки и пробелы в конце.
    text = text.rstrip(". ")

    if text.upper() in _RESERVED:
        text = f"_{text}"

    if len(text) > max_chars:
        text = text[:max_chars].rstrip(". ")
    return text


def sanitize_author(authors: str) -> str:
    """Имя автора перевода для подстановки в имя файла.

    Реальные значения из API содержат скобки и точки: «Crunchyroll [10bit]»,
    «YakuSub Studio (Aero)», «EAC-3 2.0».
    """
    cleaned = sanitize_component(authors, MAX_AUTHOR_CHARS)
    # Пробелы в имени группы мешают читать имя файла — заменяем подчёркиванием.
    cleaned = cleaned.replace(" ", "_")
    return cleaned or "unknown"


def author_marker(authors: str, translation_id: int | None = None) -> str:
    """Метка загрузки: кто перевёл и какой это перевод.

    Идентификатор перевода делает файл **самоописывающимся**: он ведёт в
    каталоге к тайтлу и серии, и эта связь сильнее папки вокруг файла. Без него
    файл, скачанный нами, опознавался только по папке и номеру серии — а
    переехавший в другую папку не опознавался никак. Сам сайт называет свои
    файлы так же (`… (t4505262).mp4`), и именно они у пользователя всегда
    привязывались точно.

    ⚠️ Номер приклеивается ПОСЛЕ `sanitize_author`. Обрезка длинного имени
    группы идёт по `MAX_AUTHOR_CHARS`, и попади номер внутрь — от него осталась
    бы половина, то есть ссылка на чужой перевод.
    """
    marker = f"anime365_{sanitize_author(authors)}"
    return f"{marker}_t{translation_id}" if translation_id else marker


def truncate_to_bytes(name: str, limit: int = MAX_FILENAME_BYTES) -> str:
    """Обрезает имя по границе символов так, чтобы уложиться в лимит байтов."""
    if len(name.encode("utf-8")) <= limit:
        return name

    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    ext_bytes = len(f".{ext}".encode()) if ext else 0
    budget = limit - ext_bytes

    out = []
    used = 0
    for char in stem:
        size = len(char.encode("utf-8"))
        if used + size > budget:
            break
        out.append(char)
        used += size

    result = "".join(out).rstrip(". ")
    return f"{result}.{ext}" if ext else result


@dataclass(frozen=True)
class NamingContext:
    """Всё, что нужно, чтобы собрать имя."""

    show_title: str
    season: int | None
    episode: float
    episode_title: str | None
    authors: str
    height: int
    lang: str
    kind: str  # voice | sub | subv | raw
    extension: str
    year: int | None = None
    is_movie: bool = False
    #: Идентификатор перевода Anime365. Попадает в имя файла — см. `author_marker`.
    translation_id: int | None = None


def _episode_code(season: int | None, episode: float) -> str:
    season_no = 1 if season is None else season
    if float(episode).is_integer():
        return f"S{season_no:02d}E{int(episode):02d}"
    # Дробные номера (7.5) встречаются у спешлов между сериями.
    return f"S{season_no:02d}E{episode:g}".replace(".", "_")


def _source_tag(height: int) -> str:
    """Метка источника. У субтитров разрешения нет — «WEBDL-0p» выглядело нелепо."""
    return f"{SOURCE_TAG}-{height}p" if height else SOURCE_TAG


def _lang_suffix(ctx: NamingContext) -> str:
    # ⚠️ Проверяем набор видов, а не `== "sub"`. Субтитровых видов в API
    # два: обычный `sub` и `subv` — надписи и песни. Со сравнением по одному
    # значению файл `subv` уезжал в библиотеку без `.ru`, и Jellyfin не понимал,
    # на каком он языке.
    if is_subtitle_kind(ctx.kind):
        return SUB_LANG_SUFFIX.get(ctx.lang, "")
    if ctx.kind == "voice":
        return VIDEO_LANG_SUFFIX.get(ctx.lang, "")
    # RAW — японский оригинал, суффикс не добавляем.
    return ""


def canonical_name(ctx: NamingContext) -> str:
    """Имя по канонической схеме — когда соседних файлов нет."""
    author = author_marker(ctx.authors, ctx.translation_id)
    suffix = _lang_suffix(ctx)

    if ctx.is_movie:
        title = sanitize_component(ctx.show_title)
        year = f" ({ctx.year})" if ctx.year else ""
        # У фильма номера серии в имени нет — он там и не нужен. Но пользователь
        # может вручную назначить «фильмом» связку из нескольких частей, и тогда
        # без номера все они получат одно имя: вторая легла бы рядом как «v2»,
        # то есть выглядела бы дублем первой. Номер добавляем только со второй
        # части, чтобы у обычного фильма имя осталось прежним.
        part = f" - {int(ctx.episode)}" if ctx.episode and ctx.episode > 1 else ""
        name = (
            f"{title}{year}{part} - [{_source_tag(ctx.height)}] "
            f"[{author}]{suffix}{ctx.extension}"
        )
        return truncate_to_bytes(name)

    show = sanitize_component(ctx.show_title)
    code = _episode_code(ctx.season, ctx.episode)
    episode_title = sanitize_component(ctx.episode_title or "")
    title_part = f" - {episode_title}" if episode_title else ""
    name = (
        f"{show} {code}{title_part} "
        f"[{author}] {_source_tag(ctx.height)}{suffix}{ctx.extension}"
    )
    return truncate_to_bytes(name)


# Шаблон имени соседнего файла: до кода серии, сам код, середина, релиз-группа, хвост.
_NEIGHBOUR_RE = re.compile(
    r"^(?P<head>.*?)"
    r"(?P<code>S\d{1,2}E\d{1,4}(?:-E\d{1,4})?)"
    r"(?P<middle>.*?)"
    r"\[(?P<group>[^\]]+)\]"
    r"(?P<tail>.*)$",
    re.IGNORECASE,
)

# Метка источника у соседа — её мы заменяем на свою, а не наследуем.
_SOURCE_TAG_RE = re.compile(
    r"(?i)\b(Bluray|BluRay|BDRip|BD|WEBRip|WEB-?DL|HDTV|DVDRip|TVRip|Remux)"
    r"(?:-\d{3,4}p)?\b"
)


def name_from_neighbour(neighbour: str, ctx: NamingContext) -> str | None:
    """Имя по образцу соседнего файла в той же папке.

    Из образца берётся структура (название шоу, положение кода серии, название
    эпизода), но релиз-группа заменяется на нашу, метка источника — на WEBDL,
    а языковой суффикс проставляется свой.
    """
    stem = Path(neighbour).stem
    # У соседа мог быть языковой суффикс — он не часть структуры.
    stem = re.sub(r"\.(ru|en|ja|jpn|eng|rus)$", "", stem, flags=re.IGNORECASE)

    match = _NEIGHBOUR_RE.match(stem)
    if not match:
        return None

    author = author_marker(ctx.authors, ctx.translation_id)
    code = _episode_code(ctx.season, ctx.episode)

    # ⚠️ Середину имени соседа НЕЛЬЗЯ переносить как есть: там лежит название
    # ЕГО эпизода, и серия 6 получила бы заголовок серии 5. Подставляем своё,
    # а если названия нет (Anime365 отдаёт episodeTitle пустым почти всегда) —
    # оставляем пусто, лучше без названия, чем с чужим.
    episode_title = sanitize_component(ctx.episode_title or "")
    middle = f" - {episode_title}" if episode_title else ""

    tail = match.group("tail")

    # Метку источника соседа выбрасываем: у нас всегда WEBDL.
    tail = _SOURCE_TAG_RE.sub("", tail)
    tail = re.sub(r"\b\d{3,4}p\b", "", tail)
    tail = _SPACES.sub(" ", tail).strip(" -")

    parts = [
        match.group("head").strip(),
        code,
        middle.strip(),
        f"[{author}]",
        _source_tag(ctx.height),
    ]
    if tail:
        parts.append(tail)

    name = " ".join(p for p in parts if p)
    name = _SPACES.sub(" ", name).strip()
    return truncate_to_bytes(f"{name}{_lang_suffix(ctx)}{ctx.extension}")


def subtitle_name_for_video(
    video_filename: str,
    authors: str,
    lang: str,
    extension: str,
    translation_id: int | None = None,
) -> str:
    """Имя субтитров к УЖЕ СУЩЕСТВУЮЩЕМУ локальному видео.

    Jellyfin подхватывает субтитры только при совпадении basename с видеофайлом,
    поэтому basename берётся как есть — вместе с чужой релиз-группой и чужой
    меткой Bluray. Правило про WEBDL здесь неприменимо: это имя не наше.

    Имя автора вставляется, чтобы на один эпизод можно было держать несколько
    переводов — иначе второй русский затирал бы первый.

    ⚠️ В имени оказываются ДВА идентификатора перевода: чужой — в основе имени,
    унаследованной от видео, — и свой, в хвосте. Свой стоит последним, и правило
    разбора `anime365_ours` ищет именно последний. Иначе субтитры получили бы
    язык и вид перевода видеодорожки.
    """
    stem = Path(video_filename).stem
    # Если у видео уже был языковой суффикс, второй добавлять не нужно.
    stem = re.sub(r"\.(ru|en|ja|jpn|eng|rus)$", "", stem, flags=re.IGNORECASE)

    marker = author_marker(authors, translation_id)
    suffix = SUB_LANG_SUFFIX.get(lang, "")
    return truncate_to_bytes(f"{stem}.{marker}{suffix}{extension}")


def pick_neighbour(filenames: list[str], video_extensions: set[str]) -> str | None:
    """Выбирает образец среди соседних файлов.

    Берём самое частое имя по структуре: в папке могут лежать и «левые» файлы,
    и ориентироваться надо на преобладающий формат.
    """
    candidates = [f for f in filenames if Path(f).suffix.lower() in video_extensions]
    if not candidates:
        return None

    # Предпочитаем файлы, чьё имя разбирается шаблоном: остальные бесполезны.
    parseable = [f for f in candidates if _NEIGHBOUR_RE.match(Path(f).stem)]
    if not parseable:
        return None

    # Самое длинное имя обычно самое полное по структуре.
    return max(parseable, key=lambda f: len(Path(f).stem))


def build_target_name(
    ctx: NamingContext,
    neighbour: str | None = None,
    existing_video: str | None = None,
) -> str:
    """Точка входа: собирает итоговое имя файла.

    `existing_video` задаётся, когда качаем субтитры к уже лежащему видео —
    этот случай имеет приоритет, потому что имя обязано совпасть.
    """
    if existing_video and is_subtitle_kind(ctx.kind):
        return subtitle_name_for_video(
            existing_video, ctx.authors, ctx.lang, ctx.extension, ctx.translation_id
        )

    if neighbour:
        from_neighbour = name_from_neighbour(neighbour, ctx)
        if from_neighbour:
            return from_neighbour
        log.debug("Образец %s не разобрался, использую каноническую схему", neighbour)

    return canonical_name(ctx)


#: Откуда берётся имя папки, по убыванию предпочтения.
#:
#: Английское ближе к тому, как названы существующие папки, но заполнено лишь
#: у 46% тайтлов — для остальных romaji работает запасным вариантом и есть
#: почти везде (99.9%). Русское — последний рубеж.
FOLDER_NAME_SOURCES = ("en", "romaji", "ru")


def _normalized(value: str) -> str:
    """Только буквы и цифры в нижнем регистре — для сравнения написаний."""
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def folder_name_source(titles: dict[str, str]) -> str | None:
    """Какое именно написание пойдёт в имя папки.

    Нужно интерфейсу: в плашке «папка будет создана» честнее назвать источник,
    чем пересказывать правило. Пользователь и так видит имя — ему интересно,
    откуда оно взялось.

    Есть одна поправка к простому порядку приоритетов. Английское название в
    каталоге нередко обрезано до части франшизы: у тайтла 27995 `en` —
    «Morfonication», тогда как romaji — «BanG Dream! Morfonication». Папка с
    именем из огрызка не встанет рядом с остальными частями франшизы и не
    сопоставится с уже существующей.

    Поэтому если romaji **содержит** английское название и длиннее его, берём
    romaji: это тот же тайтл, только названный полностью. Когда написания
    расходятся по существу («Zombie Land Saga» против «Zombieland Saga»),
    условие не срабатывает, и остаётся английское.
    """
    titles = titles or {}
    en, romaji = titles.get("en"), titles.get("romaji")

    if en and romaji:
        en_key, romaji_key = _normalized(en), _normalized(romaji)
        if en_key and en_key in romaji_key and len(romaji_key) > len(en_key):
            return "romaji"

    for key in FOLDER_NAME_SOURCES:
        if titles.get(key):
            return key
    return None


def folder_name_for_title(
    titles: dict[str, str],
    year: int | None = None,
    is_movie: bool = False,
    part: int | None = None,
) -> str:
    """Имя папки для тайтла, которого ещё нет в библиотеке.

    Приоритет — `FOLDER_NAME_SOURCES`. Схема совпадает с тем, как называют папки
    Sonarr и Radarr, чтобы Jellyfin распознал их без ручной правки.
    """
    source = folder_name_source(titles)
    raw = (titles or {}).get(source, "") if source else ""
    # В библиотеке пользователя слеш заменён на плюс: «Fate+stay night».
    # Держимся того же соглашения, иначе рядом появится папка с другим написанием.
    # Матчер нормализует «+» обратно в «/», поэтому сопоставление не пострадает.
    raw = raw.replace("/", "+")
    name = sanitize_component(raw, max_chars=150)
    if not name:
        return ""
    # У фильмов Radarr держит год прямо в имени папки.
    if is_movie and year:
        name = f"{name} ({year})"
    # Часть многосерийного фильма — отдельная папка, иначе вторая часть легла бы
    # рядом с первой и Jellyfin счёл бы её альтернативной версией того же фильма,
    # а не самостоятельным фильмом.
    #
    # Отдельных названий у частей в API нет (`episodeTitle` пуст, есть только
    # «Фильм 1 серия»), поэтому в имени остаётся номер. Не изящно, но однозначно.
    if part is not None:
        name = f"{name} - {part}"
    return name


def unique_path(folder: PurePosixPath | Path, filename: str, existing: set[str]) -> str:
    """Разводит коллизию имён, не перетирая существующий файл."""
    if filename not in existing:
        return filename

    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""

    for index in range(2, 100):
        candidate = f"{stem} v{index}.{ext}" if ext else f"{stem} v{index}"
        if candidate not in existing:
            return candidate

    # Практически недостижимо, но молча перетирать нельзя.
    raise ValueError(f"Не удалось подобрать свободное имя для {filename}")


def audio_name(ctx: NamingContext) -> str:
    """Имя файла звуковой дорожки.

    Отличается от видео двумя вещами:
      * **нет метки источника** (`WEBDL-1080p`) — у звука разрешения не бывает,
        а откуда взято видео, для фонотеки значения не имеет;
      * **язык указывается всегда**, включая японский оригинал. У видео японская
        дорожка суффикса не получает, потому что она там по умолчанию; в
        фонотеке рядом лежат дорожки разных языков, и без пометки их не различить.

    Расширение приходит готовым: оно определяется кодеком дорожки, потому что
    поток копируется без перекодирования.
    """
    author = author_marker(ctx.authors, ctx.translation_id)
    lang = ctx.lang or "ja"

    if ctx.is_movie:
        title = sanitize_component(ctx.show_title)
        year = f" ({ctx.year})" if ctx.year else ""
        name = f"{title}{year} [{author}].{lang}{ctx.extension}"
        return truncate_to_bytes(name)

    show = sanitize_component(ctx.show_title)
    code = _episode_code(ctx.season, ctx.episode)
    episode_title = sanitize_component(ctx.episode_title or "")
    title_part = f" - {episode_title}" if episode_title else ""
    name = f"{show} {code}{title_part} [{author}].{lang}{ctx.extension}"
    return truncate_to_bytes(name)
