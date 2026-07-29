"""Сопоставление локальных папок с тайтлами Anime365.

Каскады описаны в claude/05-matching-algorithm.md. Коротко:

  A — быстрые точные совпадения по ID и японскому названию (если данные есть);
  B — строковый матчинг, ОСНОВНОЙ механизм: обязан работать без .nfo и без ID;
  C — сезоны, разводятся по дате премьеры из season.nfo;
  D — всё остальное уходит на ручную привязку.

⚠️ Почему строковый путь основной, а не ID: anilist_id заполнен лишь у 19 030
тайтлов каталога из 29 231, а в .nfo соответствующих тегов может не быть вовсе.
Плюс <shikimoriid> встречается неверный.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from rapidfuzz import fuzz, process

from app.services.filename_rules import normalize_for_match, normalize_title
from app.services.nfo_parser import NfoData, candidate_titles

log = logging.getLogger(__name__)


class MatchSource(str, Enum):
    ANILIST_ID = "anilist_id"
    MAL_ID = "mal_id"
    ORIGINAL_TITLE = "original_title"
    EXACT_TITLE = "exact_title"
    PREMIERED = "premiered"
    FUZZY = "fuzzy"
    MANUAL = "manual"


# Поле season каталога выглядит как «Осень 2015». Месяц премьеры из .nfo
# отображается на квартал: зима — декабрь-февраль, весна — март-май и так далее.
_MONTH_TO_SEASON = {
    12: "Зима", 1: "Зима", 2: "Зима",
    3: "Весна", 4: "Весна", 5: "Весна",
    6: "Лето", 7: "Лето", 8: "Лето",
    9: "Осень", 10: "Осень", 11: "Осень",
}


def season_label_from_premiered(year: int, month: int) -> str:
    """«2012-07-02» → «Лето 2012». Декабрь относится к зиме следующего года."""
    name = _MONTH_TO_SEASON[month]
    # Декабрьские премьеры каталог относит к зимнему сезону следующего года.
    label_year = year + 1 if month == 12 else year
    return f"{name} {label_year}"


@dataclass
class SeriesCandidate:
    """Минимум данных о тайтле, нужный для сопоставления."""

    id: int
    titles: dict[str, str]
    all_titles: list[str]
    type: str
    year: int | None
    season: str | None
    number_of_episodes: int
    mal_id: int | None
    anilist_id: int | None


@dataclass
class MatchResult:
    series_id: int
    source: MatchSource
    confidence: float
    matched_on: str
    # Требует подтверждения в UI, а не создаётся автоматически.
    needs_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "source": self.source.value,
            "confidence": round(self.confidence, 1),
            "matched_on": self.matched_on,
            "needs_confirmation": self.needs_confirmation,
        }


class TitleIndex:
    """Индекс названий каталога для быстрого поиска.

    Строится один раз на сканирование. На 29 тысячах тайтлов это порядка
    220 тысяч нормализованных вариантов — держится в памяти без проблем.
    """

    def __init__(self, candidates: Iterable[SeriesCandidate]) -> None:
        self.by_id: dict[int, SeriesCandidate] = {}
        self.by_anilist: dict[int, int] = {}
        self.by_mal: dict[int, int] = {}
        # Нормализованное название -> список id. Список, потому что у разных
        # тайтлов названия совпадают (сезоны, ремейки, одноимённые фильмы).
        self.by_norm_title: dict[str, list[int]] = {}
        self.by_japanese: dict[str, list[int]] = {}

        for item in candidates:
            self.by_id[item.id] = item
            if item.anilist_id:
                self.by_anilist.setdefault(item.anilist_id, item.id)
            if item.mal_id:
                self.by_mal.setdefault(item.mal_id, item.id)

            japanese = (item.titles or {}).get("ja")
            if japanese:
                self.by_japanese.setdefault(normalize_title(japanese), []).append(item.id)

            for title in self._all_variants(item):
                norm = normalize_title(title)
                if norm:
                    self.by_norm_title.setdefault(norm, []).append(item.id)

        # Для fuzzy: плоский список ключей, по которому идёт поиск похожих.
        self._norm_keys = list(self.by_norm_title.keys())

    @staticmethod
    def _all_variants(item: SeriesCandidate) -> list[str]:
        out = list((item.titles or {}).values())
        out.extend(item.all_titles or [])
        return [t for t in out if t]

    def fuzzy(self, query: str, limit: int = 8) -> list[tuple[str, float]]:
        if not query or not self._norm_keys:
            return []
        found = process.extract(
            query, self._norm_keys, scorer=fuzz.token_set_ratio, limit=limit, score_cutoff=60
        )
        return [(key, score) for key, score, _ in found]


@dataclass
class MatchContext:
    """Всё, что известно о локальной папке."""

    folder_name: str
    scope: str  # series | movie
    nfo: NfoData | None = None
    season_nfo: NfoData | None = None
    local_episode_count: int = 0

    @property
    def season_number(self) -> int | None:
        if self.season_nfo is not None and self.season_nfo.season is not None:
            return self.season_nfo.season
        return None


class Matcher:
    def __init__(
        self,
        index: TitleIndex,
        auto_threshold: float = 88.0,
        suggest_threshold: float = 75.0,
    ) -> None:
        self.index = index
        self.auto_threshold = auto_threshold
        self.suggest_threshold = suggest_threshold

    def match(self, ctx: MatchContext) -> MatchResult | None:
        for step in (self._cascade_a, self._cascade_c, self._cascade_b):
            result = step(ctx)
            if result is not None:
                return result
        return None

    # ------------------------------------------------------------------ #
    # Каскад A — точные совпадения по ID и японскому названию
    # ------------------------------------------------------------------ #

    def _cascade_a(self, ctx: MatchContext) -> MatchResult | None:
        nfo = ctx.nfo
        if nfo is None:
            return None

        # Сезонные папки обрабатывает каскад C: anilist_id из tvshow.nfo
        # указывает на первый сезон, и для Season 2 он даст неверный тайтл.
        if ctx.season_number not in (None, 1):
            return None

        if nfo.anilist_id:
            series_id = self.index.by_anilist.get(nfo.anilist_id)
            if series_id is not None:
                return MatchResult(
                    series_id, MatchSource.ANILIST_ID, 100.0, f"anilistid={nfo.anilist_id}"
                )

        # shikimoriid бывает неверным, поэтому принимаем только с проверкой названия.
        if nfo.shikimori_id:
            series_id = self.index.by_mal.get(nfo.shikimori_id)
            if series_id is not None and self._title_agrees(ctx, series_id):
                return MatchResult(
                    series_id, MatchSource.MAL_ID, 95.0, f"shikimoriid={nfo.shikimori_id}"
                )

        if nfo.original_title:
            ids = self.index.by_japanese.get(normalize_title(nfo.original_title)) or []
            if len(ids) == 1:
                return MatchResult(
                    ids[0], MatchSource.ORIGINAL_TITLE, 97.0, nfo.original_title
                )
            if ids:
                narrowed = self._disambiguate(ctx, ids)
                if narrowed is not None:
                    return MatchResult(
                        narrowed, MatchSource.ORIGINAL_TITLE, 92.0, nfo.original_title
                    )

        return None

    def _title_agrees(self, ctx: MatchContext, series_id: int) -> bool:
        """Проверка, что найденный по ID тайтл хотя бы похож по названию."""
        candidate = self.index.by_id.get(series_id)
        if candidate is None:
            return False

        variants = [normalize_title(t) for t in TitleIndex._all_variants(candidate)]
        for raw in candidate_titles(ctx.nfo, ctx.folder_name):
            norm, _ = normalize_for_match(raw)
            if not norm:
                continue
            if any(fuzz.token_set_ratio(norm, v) >= self.suggest_threshold for v in variants):
                return True
        return False

    # ------------------------------------------------------------------ #
    # Каскад C — сезоны по дате премьеры
    # ------------------------------------------------------------------ #

    def _cascade_c(self, ctx: MatchContext) -> MatchResult | None:
        season_nfo = ctx.season_nfo
        if season_nfo is None:
            return None

        quarter = season_nfo.season_quarter
        if quarter is None:
            return None

        label = season_label_from_premiered(*quarter)

        # Кандидаты — тайтлы с похожим базовым названием.
        base_ids = self._string_candidates(ctx)
        if not base_ids:
            return None

        matching = [
            sid
            for sid in base_ids
            if (c := self.index.by_id.get(sid)) is not None and c.season == label
        ]
        if len(matching) == 1:
            return MatchResult(
                matching[0], MatchSource.PREMIERED, 96.0, f"{season_nfo.premiered} → {label}"
            )
        if len(matching) > 1:
            narrowed = self._disambiguate(ctx, matching)
            if narrowed is not None:
                return MatchResult(
                    narrowed, MatchSource.PREMIERED, 90.0, f"{season_nfo.premiered} → {label}"
                )
        return None

    # ------------------------------------------------------------------ #
    # Каскад B — строковый матчинг
    # ------------------------------------------------------------------ #

    def _cascade_b(self, ctx: MatchContext) -> MatchResult | None:
        for raw in candidate_titles(ctx.nfo, ctx.folder_name):
            norm, _ = normalize_for_match(raw)
            if not norm:
                continue

            ids = self.index.by_norm_title.get(norm) or []
            if len(ids) == 1:
                return MatchResult(ids[0], MatchSource.EXACT_TITLE, 99.0, raw)
            if ids:
                narrowed = self._disambiguate(ctx, ids)
                if narrowed is not None:
                    return MatchResult(narrowed, MatchSource.EXACT_TITLE, 93.0, raw)

        # Точного совпадения нет — идём в нечёткое.
        best: MatchResult | None = None
        for raw in candidate_titles(ctx.nfo, ctx.folder_name):
            norm, _ = normalize_for_match(raw)
            if not norm:
                continue

            for key, score in self.index.fuzzy(norm):
                ids = self.index.by_norm_title.get(key) or []
                target = ids[0] if len(ids) == 1 else self._disambiguate(ctx, ids)
                if target is None:
                    continue
                if best is None or score > best.confidence:
                    best = MatchResult(
                        target,
                        MatchSource.FUZZY,
                        score,
                        f"{raw} ≈ {key}",
                        needs_confirmation=score < self.auto_threshold,
                    )

        if best is not None and best.confidence >= self.suggest_threshold:
            return best
        return None

    def _string_candidates(self, ctx: MatchContext) -> list[int]:
        """Идентификаторы тайтлов, похожих по названию, — основа для каскада C."""
        found: list[int] = []
        for raw in candidate_titles(ctx.nfo, ctx.folder_name):
            norm, _ = normalize_for_match(raw)
            if not norm:
                continue
            found.extend(self.index.by_norm_title.get(norm) or [])
            for key, score in self.index.fuzzy(norm, limit=12):
                if score >= self.suggest_threshold:
                    found.extend(self.index.by_norm_title.get(key) or [])
        return list(dict.fromkeys(found))

    # ------------------------------------------------------------------ #
    # Разрешение неоднозначности
    # ------------------------------------------------------------------ #

    def _disambiguate(self, ctx: MatchContext, ids: list[int]) -> int | None:
        """Отбор единственного кандидата по году, типу и числу серий."""
        candidates = [c for sid in ids if (c := self.index.by_id.get(sid)) is not None]
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0].id

        # Тип: папка в корне фильмов не может оказаться сериалом и наоборот.
        if ctx.scope == "movie":
            filtered = [c for c in candidates if c.type == "movie"]
        else:
            filtered = [c for c in candidates if c.type != "movie"]
        candidates = filtered or candidates
        if len(candidates) == 1:
            return candidates[0].id

        # Квартал премьеры — самый сильный признак после типа.
        quarter = None
        if ctx.season_nfo is not None:
            quarter = ctx.season_nfo.season_quarter
        if quarter is None and ctx.nfo is not None:
            quarter = ctx.nfo.season_quarter
        if quarter is not None:
            label = season_label_from_premiered(*quarter)
            by_season = [c for c in candidates if c.season == label]
            if len(by_season) == 1:
                return by_season[0].id
            candidates = by_season or candidates

        # Год.
        year = (ctx.season_nfo.year if ctx.season_nfo else None) or (
            ctx.nfo.year if ctx.nfo else None
        )
        if year is not None:
            by_year = [c for c in candidates if c.year == year]
            if len(by_year) == 1:
                return by_year[0].id
            candidates = by_year or candidates

        # Число серий: сравниваем с тем, сколько файлов лежит локально.
        if ctx.local_episode_count > 0 and len(candidates) > 1:
            candidates = sorted(
                candidates,
                key=lambda c: abs((c.number_of_episodes or 0) - ctx.local_episode_count),
            )
            best, second = candidates[0], candidates[1]
            best_delta = abs((best.number_of_episodes or 0) - ctx.local_episode_count)
            second_delta = abs((second.number_of_episodes or 0) - ctx.local_episode_count)
            if best_delta < second_delta:
                return best.id

        return candidates[0].id if len(candidates) == 1 else None
