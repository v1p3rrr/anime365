"""Разбор имён файлов по правилам из config/filename_rules.yaml.

Регэкспы намеренно вынесены из кода: библиотека собиралась годами разными
инструментами, и предсказать все варианты именования заранее невозможно.
Правила дописываются под реальный список файлов без пересборки образа,
а корпус тестов не даёт новому правилу сломать уже разобранное.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

CONFIG_PATH = Path("/config/filename_rules.yaml")
# Запасной путь для запуска из исходников без контейнера.
FALLBACK_PATH = Path(__file__).resolve().parents[3] / "config" / "filename_rules.yaml"


@dataclass(frozen=True)
class Rule:
    id: str
    scope: str
    priority: int
    pattern: re.Pattern[str]
    enabled: bool = True

    def matches_scope(self, scope: str) -> bool:
        return self.scope == "any" or scope == "any" or self.scope == scope


@dataclass
class ParsedName:
    """Результат разбора одного имени."""

    raw: str
    matched_rule_id: str | None = None
    season: int | None = None
    episode: float | None = None
    episode_end: float | None = None
    title: str | None = None
    year: int | None = None
    translation_id: int | None = None
    release_group: str | None = None
    language: str | None = None
    excluded_by: str | None = None

    @property
    def is_parsed(self) -> bool:
        return self.episode is not None or self.year is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "matched_rule_id": self.matched_rule_id,
            "season": self.season,
            "episode": self.episode,
            "episode_end": self.episode_end,
            "title": self.title,
            "year": self.year,
            "translation_id": self.translation_id,
            "release_group": self.release_group,
            "language": self.language,
            "excluded_by": self.excluded_by,
            "is_parsed": self.is_parsed,
        }


@dataclass
class RuleSet:
    rules: list[Rule] = field(default_factory=list)
    exclude: list[re.Pattern[str]] = field(default_factory=list)
    technical_noise: list[re.Pattern[str]] = field(default_factory=list)
    release_group: list[re.Pattern[str]] = field(default_factory=list)
    language_suffixes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    video_ext: frozenset[str] = frozenset()
    subtitle_ext: frozenset[str] = frozenset()
    metadata_ext: frozenset[str] = frozenset()


def _compile(patterns: list[str], where: str) -> list[re.Pattern[str]]:
    out = []
    for raw in patterns or []:
        try:
            out.append(re.compile(raw))
        except re.error as exc:
            # Битое правило не должно ронять сканирование целиком.
            log.error("Пропускаю некорректный шаблон в %s: %s (%s)", where, raw, exc)
    return out


def load_rules(path: Path | None = None) -> RuleSet:
    source = path or (CONFIG_PATH if CONFIG_PATH.is_file() else FALLBACK_PATH)
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}

    rules: list[Rule] = []
    for item in data.get("episode_rules") or []:
        if not item.get("enabled", True):
            continue
        try:
            compiled = re.compile(item["pattern"])
        except (re.error, KeyError) as exc:
            log.error("Пропускаю правило %s: %s", item.get("id"), exc)
            continue
        rules.append(
            Rule(
                id=item["id"],
                scope=item.get("scope", "any"),
                priority=int(item.get("priority", 0)),
                pattern=compiled,
            )
        )
    rules.sort(key=lambda r: -r.priority)

    ext = data.get("extensions") or {}
    langs = {
        key: tuple(str(v).lower() for v in values)
        for key, values in (data.get("language_suffixes") or {}).items()
    }

    return RuleSet(
        rules=rules,
        exclude=_compile(data.get("exclude_patterns"), "exclude_patterns"),
        technical_noise=_compile(data.get("technical_noise"), "technical_noise"),
        release_group=_compile(data.get("release_group_patterns"), "release_group_patterns"),
        language_suffixes=langs,
        video_ext=frozenset(e.lower() for e in ext.get("video", [])),
        subtitle_ext=frozenset(e.lower() for e in ext.get("subtitle", [])),
        metadata_ext=frozenset(e.lower() for e in ext.get("metadata", [])),
    )


@lru_cache(maxsize=1)
def get_rules() -> RuleSet:
    return load_rules()


def reload_rules() -> RuleSet:
    """Сбрасывает кэш — вызывается после правки конфига."""
    get_rules.cache_clear()
    return get_rules()


# --------------------------------------------------------------------------- #
# Разбор
# --------------------------------------------------------------------------- #


def _to_number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def strip_language_suffix(stem: str, rules: RuleSet) -> tuple[str, str | None]:
    """Отделяет языковой суффикс субтитров от основы имени.

    Реальные примеры разнобоя из библиотеки: рядом лежат `.jpn.srt`, `.ja.srt`,
    `.3.ja.srt` и голый `.srt`. Числовые вставки перед языком отбрасываем,
    голый суффикс оставляем как «язык неизвестен».
    """
    parts = stem.split(".")
    language: str | None = None

    while len(parts) > 1:
        tail = parts[-1].lower()

        # Числовая вставка вида "....3.ja.srt" — просто отбрасываем.
        if tail.isdigit() and len(tail) <= 2:
            parts.pop()
            continue

        matched = next(
            (code for code, aliases in rules.language_suffixes.items() if tail in aliases),
            None,
        )
        if matched is None:
            break
        language = matched
        parts.pop()

    return ".".join(parts), language


def detect_release_group(name: str, rules: RuleSet) -> str | None:
    for pattern in rules.release_group:
        match = pattern.search(name)
        if match:
            group = (match.groupdict().get("group") or "").strip()
            # Отсекаем технические скобки вроде [1080p] и [10bit].
            if group and not any(noise.search(group) for noise in rules.technical_noise):
                return group
    return None


def parse_filename(name: str, scope: str = "any", rules: RuleSet | None = None) -> ParsedName:
    """Разбирает имя файла. `scope` — series или movie, по корню библиотеки."""
    rules = rules or get_rules()
    path = Path(name)
    suffix = path.suffix.lower()
    stem = path.stem

    base, language = strip_language_suffix(stem, rules)
    result = ParsedName(raw=name, language=language)

    # Часть релизов использует подчёркивания вместо пробелов: "New Game!_-_NCOP_(...)".
    # Подчёркивание — словесный символ, поэтому \b внутри него не срабатывает,
    # и правила молча не находят ни номер серии, ни маркеры OP/ED.
    base = base.replace("_", " ")

    for pattern in rules.exclude:
        if pattern.search(base):
            result.excluded_by = pattern.pattern
            return result

    for rule in rules.rules:
        if not rule.matches_scope(scope):
            continue
        match = rule.pattern.search(base)
        if not match:
            continue

        groups = match.groupdict()
        result.matched_rule_id = rule.id
        result.season = int(groups["season"]) if groups.get("season") else None
        result.episode = _to_number(groups.get("episode"))
        result.episode_end = _to_number(groups.get("episode_end"))
        result.year = int(groups["year"]) if groups.get("year") else None
        result.translation_id = (
            int(groups["translation_id"]) if groups.get("translation_id") else None
        )
        if groups.get("title"):
            result.title = clean_title(groups["title"], rules)
        break

    result.release_group = detect_release_group(base, rules)
    if result.title is None:
        result.title = clean_title(base, rules)

    # Расширение уточняет тип файла, но на разбор номера не влияет.
    if suffix and suffix not in rules.video_ext | rules.subtitle_ext | rules.metadata_ext:
        log.debug("Незнакомое расширение %s у %s", suffix, name)

    return result


# --------------------------------------------------------------------------- #
# Нормализация названий (используется матчером)
# --------------------------------------------------------------------------- #

# Юникодные римские цифры: в movie.nfo встречается "Ⅰ.presage flower".
_ROMAN_MAP = {
    "Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V",
    "Ⅵ": "VI", "Ⅶ": "VII", "Ⅷ": "VIII", "Ⅸ": "IX", "Ⅹ": "X",
    "ⅰ": "i", "ⅱ": "ii", "ⅲ": "iii", "ⅳ": "iv", "ⅴ": "v",
    "ⅵ": "vi", "ⅶ": "vii", "ⅷ": "viii", "ⅸ": "ix", "ⅹ": "x",
}

_DECORATIVE = "☆★・△○□♪＊*~〜"

_BRACKETS_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")
_SPACES_RE = re.compile(r"\s+")

# Обозначения сезона в имени папки. Нужны отдельным признаком, а не частью
# названия: иначе "Yuru Yuri♪♪" и "Yuru Yuri" станут неразличимы после чистки.
_SEASON_PATTERNS = (
    re.compile(r"(?i)\bseason\s*(\d{1,2})\b"),
    re.compile(r"(?i)\bs(\d{1,2})\b"),
    re.compile(r"(?i)\b(\d{1,2})(?:nd|rd|th|st)\s+season\b"),
    re.compile(r"(?i)\b(\d{1,2})\s*сезон\b"),
)


def clean_title(value: str, rules: RuleSet | None = None) -> str:
    """Убирает технический хвост и скобочные блоки, оставляя само название."""
    rules = rules or get_rules()
    text = value
    for pattern in rules.technical_noise:
        text = pattern.sub(" ", text)
    text = _BRACKETS_RE.sub(" ", text)
    text = text.replace("_", " ")
    return _SPACES_RE.sub(" ", text).strip(" -–—.")


def extract_season(value: str) -> tuple[str, int | None]:
    """Выделяет номер сезона, возвращая остаток названия и сам номер."""
    season: int | None = None
    text = value

    for pattern in _SEASON_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                season = int(match.group(1))
            except (ValueError, IndexError):
                continue
            text = text[: match.start()] + " " + text[match.end() :]
            break

    # "Yuru Yuri♪♪" — второй сезон обозначен повтором символа.
    if season is None and "♪♪" in text:
        season = 2

    return _SPACES_RE.sub(" ", text).strip(" -–—."), season


def normalize_title(value: str) -> str:
    """Приводит название к виду, пригодному для точного сравнения.

    Учитывает реальные особенности библиотеки: `+` вместо `/` в именах папок
    (`Fate+stay night`), юникодные римские цифры и декоративные символы.
    """
    if not value:
        return ""

    text = value
    for src, dst in _ROMAN_MAP.items():
        text = text.replace(src, dst)

    # В именах файлов "/" запрещён, поэтому его заменяют на "+".
    text = text.replace("+", "/")
    text = text.replace("’", "'").replace("`", "'")

    text = unicodedata.normalize("NFKC", text)
    # Снимаем диакритику: "Pokémon" и "Pokemon" должны совпадать.
    text = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))

    for char in _DECORATIVE:
        text = text.replace(char, " ")

    text = text.lower()
    # Пунктуацию убираем целиком: двоеточия и дефисы расставляют кто как хочет.
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return _SPACES_RE.sub(" ", text).strip()


def normalize_for_match(value: str, rules: RuleSet | None = None) -> tuple[str, int | None]:
    """Полная подготовка названия к сопоставлению: чистка, сезон, нормализация."""
    cleaned = clean_title(value, rules)
    without_season, season = extract_season(cleaned)
    return normalize_title(without_season), season
