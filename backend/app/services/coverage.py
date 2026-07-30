"""Сводка переводов по тайтлу: группировка, покрытие серий, оценка качества.

Задача — показать «что вообще есть у этого тайтла» одним экраном, не делая при этом
по запросу на каждый перевод. У популярного тайтла их около двух тысяч.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

# Нас интересуют только эти языки; voiceOther и typeLang == "er" отбрасываем.
INTERESTING_LANGS = ("ru", "en", "ja")

# Типы эпизодов, которые не являются контентом: трейлеры, реклама, промо.
NON_CONTENT_EPISODE_TYPES = frozenset({"preview", "pv", "cm"})

# Контент, но не основной сезон. Обычно нужны именно основные серии, поэтому
# в батч спешлы и OVA попадают только по явному согласию пользователя.
EXTRA_EPISODE_TYPES = frozenset({"special", "ova"})

# Потолок скачивания. Проверено: у источников 2160p ступени download всё равно
# [1080, 720, 360] — 4K скачать нельзя. Поэтому оценку качества обрезаем здесь же,
# иначе в сводке отображалось бы «2160p», которого пользователь никогда не получит.
MAX_DOWNLOAD_HEIGHT = 1080


def format_ranges(numbers: Iterable[float]) -> str:
    """Схлопывает номера серий в диапазоны: 1,2,3,5,6 → «1–3, 5–6».

    Требование ТЗ: покрытие показывается как «на все серии» либо промежутками
    «с 1 по 19, с 22 по 25», иначе список из 25 номеров нечитаем.
    """
    unique = sorted({n for n in numbers if n is not None})
    if not unique:
        return ""

    parts: list[str] = []
    start = prev = unique[0]

    for current in unique[1:]:
        # Разрыв считаем по шагу больше единицы; дробные номера (7.5) рвут диапазон.
        if current - prev == 1 and float(prev).is_integer() and float(current).is_integer():
            prev = current
            continue
        parts.append(_fmt_range(start, prev))
        start = prev = current

    parts.append(_fmt_range(start, prev))
    return ", ".join(parts)


def _fmt_num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _fmt_range(start: float, end: float) -> str:
    return _fmt_num(start) if start == end else f"{_fmt_num(start)}–{_fmt_num(end)}"


@dataclass
class AuthorGroup:
    """Один автор (студия/команда) внутри пары «язык + тип»."""

    authors: str
    translation_ids: list[int] = field(default_factory=list)
    # Переводы спешлов и OVA лежат отдельно: тумблер «включая спешлы» в батче
    # обязан менять СОСТАВ задач, а не только своё положение. Разделение делаем
    # здесь, потому что тип эпизода известен только на сервере.
    extra_translation_ids: list[int] = field(default_factory=list)
    episode_numbers: list[float] = field(default_factory=list)
    # Максимальная высота исходников этого автора — ОЦЕНКА, не точные ступени.
    max_height: int | None = None
    # Ссылка на самую раннюю серию этого автора: кнопка «смотреть» должна вести
    # на страницу конкретного перевода, а не на общую страницу тайтла.
    first_url: str | None = None
    first_episode: float | None = None

    def to_dict(self, total_episodes: int) -> dict:
        covered = {n for n in self.episode_numbers if n is not None}
        complete = total_episodes > 0 and len(covered) >= total_episodes
        return {
            "authors": self.authors,
            "count": len(self.translation_ids),
            "translation_ids": self.translation_ids,
            "extra_translation_ids": self.extra_translation_ids,
            "coverage": "все серии" if complete else format_ranges(covered),
            "is_complete": complete,
            "max_height_estimate": self.max_height,
            "first_url": self.first_url,
            "first_episode": self.first_episode,
        }


def build_summary(translations: list[dict], total_episodes: int) -> list[dict]:
    """Группировка переводов: язык → тип → автор.

    На выходе структура, которую UI рисует аккордеонами: содержимое раскрывается
    по требованию, поэтому две тысячи переводов не попадают в DOM разом.
    """
    grouped: dict[tuple[str, str], dict[str, AuthorGroup]] = defaultdict(dict)

    for item in translations:
        lang = item.get("type_lang") or ""
        kind = item.get("type_kind") or ""
        if lang not in INTERESTING_LANGS:
            continue

        authors = (item.get("authors_summary") or "").strip() or "без указания автора"
        bucket = grouped[(lang, kind)]
        group = bucket.get(authors)
        if group is None:
            group = AuthorGroup(authors=authors)
            bucket[authors] = group

        # Спешлы и OVA — тоже контент, но не основной сезон. Держим их в
        # отдельном списке, чтобы тумблер в батче реально менял состав задач.
        if (item.get("episode_type") or "") in EXTRA_EPISODE_TYPES:
            group.extra_translation_ids.append(item["id"])
        else:
            group.translation_ids.append(item["id"])

        episode_num = item.get("episode_num")
        if episode_num is not None:
            group.episode_numbers.append(episode_num)

        # Запоминаем ссылку на самую раннюю серию: с неё логично начинать просмотр.
        url = item.get("url")
        if url and (
            group.first_url is None
            or (episode_num is not None and (group.first_episode is None or episode_num < group.first_episode))
        ):
            group.first_url = url
            group.first_episode = episode_num

        height = item.get("height")
        if height and (group.max_height is None or height > group.max_height):
            group.max_height = height

    out: list[dict] = []
    for (lang, kind), authors_map in grouped.items():
        groups = sorted(
            (g.to_dict(total_episodes) for g in authors_map.values()),
            # Сначала полные переводы, затем более широкие по покрытию.
            key=lambda g: (not g["is_complete"], -g["count"]),
        )
        out.append(
            {
                "lang": lang,
                "kind": kind,
                "total_translations": sum(g["count"] for g in groups),
                "groups": groups,
            }
        )

    # Устойчивый порядок: сначала русский, потом английский, потом японский.
    kind_order = {"voice": 0, "sub": 1, "raw": 2}
    out.sort(key=lambda s: (INTERESTING_LANGS.index(s["lang"]), kind_order.get(s["kind"], 9)))
    return out


def build_quality_summary(
    translations: list[dict],
    total_episodes: int,
    exact: dict[float, list[int]] | None = None,
) -> dict:
    """Доступное качество по сериям.

    По умолчанию это ОЦЕНКА по `height` исходника из списочного ответа: точные
    ступени скачивания из него не выводятся — встречается и 526 → [478], и
    528 → [720, 360], а у 4K-источников потолок всё равно 1080.

    `exact` — уже уточнённые кнопкой ступени из кэша embed. Для серий, которые в
    нём есть, берётся настоящий максимум; остальные остаются оценкой, и сводка
    честно помечается смешанной.
    """
    exact = exact or {}
    best_by_episode: dict[float, int] = {}
    had_above_ceiling = False
    for item in translations:
        episode = item.get("episode_num")
        height = item.get("height")
        if episode is None or not height:
            continue
        if height > MAX_DOWNLOAD_HEIGHT:
            had_above_ceiling = True
            height = MAX_DOWNLOAD_HEIGHT
        if height > best_by_episode.get(episode, 0):
            best_by_episode[episode] = height

    # Точные данные перекрывают оценку: ради этого кнопка и нужна.
    for episode, heights in exact.items():
        if heights:
            best_by_episode[episode] = max(heights)

    if not best_by_episode:
        return {
            "is_estimate": True,
            "uniform": None,
            "buckets": [],
            "unknown_episodes": total_episodes,
            "capped_from_4k": False,
        }

    # Группируем серии по одинаковому максимальному качеству.
    by_height: dict[int, list[float]] = defaultdict(list)
    for episode, height in best_by_episode.items():
        by_height[height].append(episode)

    buckets = [
        {"height": height, "episodes": format_ranges(episodes), "count": len(episodes)}
        for height, episodes in sorted(by_height.items(), reverse=True)
    ]

    known = len(best_by_episode)
    refined = len([e for e in exact if e in best_by_episode])
    return {
        # Оценка, пока хоть одна серия не уточнена: смешивать точные и
        # прикидочные цифры без пометки нельзя — пользователь примет их за факт.
        "is_estimate": refined < known,
        "refined_episodes": refined,
        # Если качество одинаково у всех серий, UI напишет одной строкой.
        "uniform": buckets[0]["height"] if len(buckets) == 1 else None,
        "buckets": buckets,
        "unknown_episodes": max(0, total_episodes - known),
        # UI может пояснить, почему у 4K-релиза показано 1080.
        "capped_from_4k": had_above_ceiling,
    }


def resolve_height(preferred: int, available: list[int]) -> int | None:
    """Выбор ступени: максимальная не выше желаемой, иначе максимальная из доступных ниже.

    Так «1080» на переводе с потолком 360 честно скачает 360, а не упадёт с ошибкой.
    """
    if not available:
        return None
    suitable = [h for h in available if h <= preferred]
    return max(suitable) if suitable else max(available)


def effective_episode_total(number_of_episodes: int, api_episodes: int, local_max: int = 0) -> int:
    """Сколько серий считать «всеми».

    numberOfEpisodes — это план, а не выложенное: у онгоингов в API эпизодов заметно
    меньше. А локально серий может оказаться больше, чем знает сайт — по ТЗ в этом
    случае показываем локальное число.
    """
    return max(number_of_episodes or 0, api_episodes or 0, local_max or 0)
