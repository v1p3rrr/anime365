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

# Служебные типы каталога: реклама, промо, клипы. Локальной папкой с аниме они
# быть не могут, а в нечётком поиске выигрывали у настоящего сериала —
# «Zombie Land Saga» уезжала в «Curry Meshi x Zombieland Saga» типа cm.
SERVICE_TYPES = frozenset({"cm", "pv", "music"})


def season_label_from_premiered(year: int, month: int) -> str:
    """«2012-07-02» → «Лето 2012». Декабрь относится к зиме следующего года."""
    name = _MONTH_TO_SEASON[month]
    # Декабрьские премьеры каталог относит к зимнему сезону следующего года.
    label_year = year + 1 if month == 12 else year
    return f"{name} {label_year}"


#: Источники, приходящие из разметки Jellyfin. Отвергнутый источник из этого
#: набора — повод пометить привязку: значит, мы разошлись с тем, что записано
#: в `.nfo`, и человеку стоит на это взглянуть.
ID_SOURCES = frozenset({MatchSource.ANILIST_ID, MatchSource.MAL_ID})

#: Ниже этого сходства названия кандидат отвергается, если подтвердить нечем.
#:
#: 45 — не наугад: на живой библиотеке `Your Name` против рекламы воды даёт 35,
#: а верный, но иначе романизированный `Genshiken` против «Gendai Shikaku Bunka
#: Kenkyuu Kai» — 100, потому что сверяемся со **всеми** написаниями каталога,
#: включая `allTitles`.
TITLE_FLOOR = 45.0

#: Ниже этого — привязку ставим, но с пометкой «ждёт подтверждения».
SUGGEST_TITLE_FLOOR = 70.0

#: Вклад похожести названия в итоговую оценку.
TITLE_WEIGHT = 0.35

#: Насколько кандидаты должны отличаться, чтобы выбор считался однозначным.
#: У «Golden Time» оба идентификатора дают по 100 похожести — развести их
#: нечем, и такую пару надо показывать человеку, а не решать за него.
AMBIGUITY_MARGIN = 8.0

#: Оба плагина Jellyfin независимо сошлись на одном тайтле.
BOTH_IDS_BONUS = 12.0

YEAR_BONUS = 6.0
YEAR_PENALTY = 5.0
EPISODE_BONUS = 4.0
#: Скачано может быть не всё, поэтому небольшое превышение допустимо.
EPISODE_SLACK = 2

#: Начиная со скольких файлов папка заведомо не «тайтл с одним эпизодом».
IMPLAUSIBLE_VIDEO_COUNT = 3

#: Сколько серий допустимо у тайтла, лежащего в корне фильмов.
MOVIE_EPISODE_LIMIT = 6


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
                if not norm:
                    continue
                bucket = self.by_norm_title.setdefault(norm, [])
                # Без проверки один тайтл попадает в список несколько раз: одинаковое
                # нормализованное название приходит и из titles, и из allTitles.
                # Тогда «ровно один кандидат» никогда не выполняется, и точное
                # совпадение всегда проваливается в нечёткий поиск.
                if item.id not in bucket:
                    bucket.append(item.id)

        for ids in self.by_japanese.values():
            ids[:] = list(dict.fromkeys(ids))

        # Для fuzzy: плоский список ключей, по которому идёт поиск похожих.
        self._norm_keys = list(self.by_norm_title.keys())

    @staticmethod
    def _all_variants(item: SeriesCandidate) -> list[str]:
        out = list((item.titles or {}).values())
        out.extend(item.all_titles or [])
        return [t for t in out if t]

    def fuzzy(self, query: str, limit: int = 8) -> list[tuple[str, float]]:
        """Похожие названия.

        Используется token_sort_ratio, а НЕ token_set_ratio: последний считает
        совпадение подмножества полным, поэтому ключ «zombie» получал те же 100
        баллов, что и «zombie land saga», и короткие мусорные названия выигрывали
        у правильного тайтла. token_sort_ratio штрафует за недостающие слова.
        """
        if not query or not self._norm_keys:
            return []
        found = process.extract(
            query, self._norm_keys, scorer=fuzz.token_sort_ratio, limit=limit, score_cutoff=60
        )
        return [(key, score) for key, score, _ in found]

    def by_prefix(self, prefix: str) -> list[int]:
        """Все тайтлы, чьё название начинается с указанного.

        Нужно каскаду сезонов: сиквелы называются «{база} 2nd Season»,
        «{база} Revenge», «{база} San☆Hai!». Полагаться на топ-N нечёткого
        поиска нельзя — при частом названии нужный сиквел в него не попадает.
        """
        if not prefix:
            return []
        out: list[int] = []
        for key, ids in self.by_norm_title.items():
            if key.startswith(prefix):
                out.extend(ids)
        return list(dict.fromkeys(out))


@dataclass
class MatchContext:
    """Всё, что известно о локальной папке.

    ⚠️ `nfo` у дочерней папки — это `.nfo` **родителя**, а `folder_name` — имя
    родителя. Так задумано для сезонов: у `Season 2` своих названий и ID нет,
    они есть только у сериала. Но у дочерней папки, которая **не сезон**
    (фильм, спешлы, `EXTRA`, `subtitles`), родительские данные — чужие, и
    принимать их за свои нельзя: именно так 20 папок получили `anilistid`
    родителя со стопроцентной уверенностью
    ([14-mapping-audit.md](../../../claude/14-mapping-audit.md)).

    Поэтому рядом лежат `own_nfo` и `own_name` — то, что принадлежит самой
    папке, — и `is_child`, по которому видно, унаследованы ли `nfo`/`folder_name`.
    """

    folder_name: str
    scope: str  # series | movie
    nfo: NfoData | None = None
    season_nfo: NfoData | None = None
    local_episode_count: int = 0
    #: Собственные имя и `.nfo` папки — в отличие от унаследованных выше.
    own_name: str = ""
    own_nfo: NfoData | None = None
    #: Есть ли родитель. Без этого «нет номера сезона» у папки верхнего уровня
    #: и у служебной подпапки выглядят одинаково, а означают противоположное.
    is_child: bool = False
    #: Сколько видеофайлов лежит в папке. Нужен для проверки правдоподобия:
    #: тайтл с одним эпизодом не может занимать папку с 26 файлами.
    video_count: int = 0

    @property
    def season_number(self) -> int | None:
        if self.season_nfo is not None and self.season_nfo.season is not None:
            return self.season_nfo.season
        return None

    @property
    def inherits_parent(self) -> bool:
        """Пользуемся ли мы данными родителя как своими.

        Только сезоны: у подпапки с номером сезона родительские название и ID
        осмысленны. Всё остальное — фильм, спешлы, бонусы, служебные папки —
        обязано опираться на себя.
        """
        return self.is_child and self.season_number is not None


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
        """Каскады предлагают кандидатов, решает сверка.

        ⚠️ Раньше побеждал **первый сработавший каскад**, и его уверенность
        была константой: нашёлся `anilistid` — 100%, и перебить это было нечем.
        Проверка на живой библиотеке показала, чем это кончается: папка
        `Your Name` села на рекламу минеральной воды, `Evangelion/Season 1`
        с 26 сериями — на показ к 30-летию с одним эпизодом, а папка
        `subtitles` — на тайтл родителя. Всё это со стопроцентной уверенностью
        ([14-mapping-audit.md](../../../claude/14-mapping-audit.md)).

        Причина не в каскадах, а в том, что источник совпадения решал всё, а
        данные — ничего. Теперь наоборот: каскады только **предлагают**, а
        каждое предложение сверяется с тем, что мы про папку знаем (название,
        год, тип, число файлов). Источник задаёт лишь стартовый вес.
        """
        proposals = self._propose(ctx)
        if not proposals:
            return None

        scored: list[tuple[float, MatchResult]] = []
        refused: list[MatchResult] = []
        for proposal in proposals:
            verdict = self._verify(proposal, ctx)
            if verdict is not None:
                scored.append(verdict)
            else:
                refused.append(proposal)

        if not scored:
            return None

        scored.sort(key=lambda item: -item[0])
        best_score, best = scored[0]

        # Второй кандидат вплотную — значит признаки не развели их, и молча
        # выбирать первого нельзя. Разница в один балл здесь ничего не значит:
        # у «Golden Time» оба кандидата дают одинаковую похожесть названия.
        runner_up = scored[1][0] if len(scored) > 1 else None
        if runner_up is not None and best_score - runner_up < AMBIGUITY_MARGIN:
            if scored[1][1].series_id != best.series_id:
                best.needs_confirmation = True

        # ⚠️ Мы отвергли идентификатор, который дала разметка, и взяли другого
        # кандидата — то есть переголосовали Jellyfin. Делать это молча нельзя.
        #
        # Исправлять чужую ошибку идентификации не наша задача: если разметка
        # называет папку рекламой воды, никакая эвристика с нашей стороны не
        # должна «знать лучше» — так ломаются верные случаи. Но и повторять
        # ошибку с уверенностью 100% нельзя: тогда её не видно, пока файлы не
        # уедут не туда. Поэтому берём того, кто сходится с названием, и
        # помечаем: пусть человек посмотрит и, если что, поправит **в
        # Jellyfin** — там источник, а у нас лишь его отражение.
        if any(item.source in ID_SOURCES for item in refused) and best.source not in ID_SOURCES:
            best.needs_confirmation = True

        return best

    # ------------------------------------------------------------------ #
    # Сбор кандидатов и их сверка
    # ------------------------------------------------------------------ #

    def _propose(self, ctx: MatchContext) -> list[MatchResult]:
        """Все правдоподобные кандидаты, а не первый попавшийся."""
        out: list[MatchResult] = []
        out.extend(self._propose_ids(ctx))
        for cascade in (self._cascade_c, self._cascade_b):
            result = cascade(ctx)
            if result is not None:
                out.append(result)

        # Один и тот же тайтл мог прийти из нескольких источников. Дубли не
        # выбрасываем молча: совпадение источников — сильный сигнал, и его
        # учитывает сверка.
        seen: dict[tuple[int, MatchSource], MatchResult] = {}
        for item in out:
            seen.setdefault((item.series_id, item.source), item)
        return list(seen.values())

    def _propose_ids(self, ctx: MatchContext) -> list[MatchResult]:
        """Кандидаты по внешним идентификаторам — **оба**, а не первый.

        ⚠️ Прежде `anilistid` выигрывал молча, а `shikimoriid` даже не
        рассматривался, если первый сработал. Замер на 236 папках показал, что
        так нельзя: у 66 из них идентификаторы указывают на **разные** тайтлы,
        и ближе к правде оказывается то один, то другой (anilist 20 раз,
        mal — 12). Поэтому предлагаем обоих и даём решить сверке.

        Порядок плагинов Jellyfin (AniList первым, затем TMDB, Shikimori,
        Kitsu, AniDB) означает, что `title` и `anilistid` пишет один плагин и
        они согласованы между собой, а `shikimoriid` матчил другой, независимо.
        Отсюда чуть больший стартовый вес у anilist — но именно вес, а не
        безусловная победа.
        """
        nfo = ctx.nfo
        if nfo is None:
            return []

        # Данные родителя годятся только сезонам. У папки без номера сезона
        # (фильм, спешлы, `EXTRA`, `subtitles`) это чужие идентификаторы.
        if ctx.is_child and not ctx.inherits_parent:
            nfo = ctx.own_nfo
            if nfo is None:
                return []

        # Сезон со вторым и дальше номером обрабатывает каскад C: ID из
        # tvshow.nfo указывает на первый сезон.
        if ctx.season_number not in (None, 1):
            return []

        out: list[MatchResult] = []
        if nfo.anilist_id:
            series_id = self.index.by_anilist.get(nfo.anilist_id)
            if series_id is not None:
                out.append(
                    MatchResult(
                        series_id, MatchSource.ANILIST_ID, 100.0, f"anilistid={nfo.anilist_id}"
                    )
                )

        if nfo.shikimori_id:
            series_id = self.index.by_mal.get(nfo.shikimori_id)
            if series_id is not None:
                out.append(
                    MatchResult(
                        series_id, MatchSource.MAL_ID, 95.0, f"shikimoriid={nfo.shikimori_id}"
                    )
                )

        out.extend(self._propose_japanese(ctx, nfo))
        return out

    def _propose_japanese(self, ctx: MatchContext, nfo: NfoData) -> list[MatchResult]:
        """Кандидат по японскому названию — оно совпадает точно или никак."""
        if not nfo.original_title:
            return []

        ids = self.index.by_japanese.get(normalize_title(nfo.original_title)) or []
        if len(ids) == 1:
            return [MatchResult(ids[0], MatchSource.ORIGINAL_TITLE, 97.0, nfo.original_title)]
        if ids:
            narrowed = self._disambiguate(ctx, ids)
            if narrowed is not None:
                return [
                    MatchResult(narrowed, MatchSource.ORIGINAL_TITLE, 92.0, nfo.original_title)
                ]
        return []

    def _verify(
        self, proposal: MatchResult, ctx: MatchContext
    ) -> tuple[float, MatchResult] | None:
        """Сверяет кандидата с тем, что мы знаем о папке.

        Возвращает оценку и поправленный результат либо None, если кандидат
        отвергнут. Отвергать важнее, чем оценивать: неверная привязка не видна,
        пока файл не уедет не в ту папку.
        """
        candidate = self.index.by_id.get(proposal.series_id)
        if candidate is None:
            return None

        title = self._title_score(ctx, candidate)
        episodes = candidate.number_of_episodes or 0

        # Правдоподобие по числу файлов. Тайтл с одним эпизодом не может
        # занимать папку с двумя десятками серий — именно так `Evangelion/Season 1`
        # (26 файлов) сел на показ к 30-летию с единственным эпизодом.
        if episodes == 1 and ctx.video_count >= IMPLAUSIBLE_VIDEO_COUNT:
            return None

        # Папка в корне фильмов не может быть сериалом на дюжину серий.
        if ctx.scope == "movie" and episodes > MOVIE_EPISODE_LIMIT:
            return None

        # Название не сошлось и подтвердить нечем — отказ. Так отсеивается
        # `Your Name` → реклама минеральной воды: идентификатор в .nfo верный,
        # а вот у Anime365 он проставлен чужому тайтлу.
        if title < TITLE_FLOOR and not self._ids_agree(ctx, proposal.series_id):
            return None

        score = proposal.confidence + (title - TITLE_FLOOR) * TITLE_WEIGHT

        if self._ids_agree(ctx, proposal.series_id):
            # Оба плагина Jellyfin независимо пришли к одному тайтлу.
            score += BOTH_IDS_BONUS

        year = self._expected_year(ctx)

        # У сезона есть собственная дата премьеры, и она — самый надёжный
        # признак: кандидат, вышедший в другом году, это не он. Без этой
        # проверки строковый каскад для `Season 2` уверенно предлагает первый
        # сезон — название-то совпадает лучше некуда.
        if ctx.season_number and ctx.season_number > 1 and year and candidate.year:
            if abs(candidate.year - year) > 1:
                return None

        if year and candidate.year:
            drift = abs(candidate.year - year)
            score += YEAR_BONUS if drift <= 1 else -YEAR_PENALTY * min(drift, 4)

        if episodes and ctx.video_count:
            # Точного равенства не требуем: скачано может быть не всё.
            if ctx.video_count <= episodes + EPISODE_SLACK:
                score += EPISODE_BONUS

        result = MatchResult(
            proposal.series_id,
            proposal.source,
            min(proposal.confidence, max(score, 0.0)),
            proposal.matched_on,
            proposal.needs_confirmation,
        )
        # Слабое совпадение по названию оставляем, но с пометкой: пусть человек
        # посмотрит, а не узнаёт об ошибке по уехавшим файлам.
        if title < SUGGEST_TITLE_FLOOR:
            result.needs_confirmation = True
        return score, result

    @staticmethod
    def _expected_year(ctx: MatchContext) -> int | None:
        """Год, которому должен соответствовать тайтл.

        У сезонной папки берётся её собственный год — он и разводит сезоны. Год
        из `<premiered>` считается наравне с `<year>`: в `season.nfo` бывает
        только дата.
        """
        for source in (ctx.season_nfo, ctx.own_nfo, ctx.nfo):
            if source is None:
                continue
            if source.year:
                return source.year
            quarter = source.season_quarter
            if quarter:
                return quarter[0]
        return None

    def _ids_agree(self, ctx: MatchContext, series_id: int) -> bool:
        """Оба идентификатора из `.nfo` указывают на этот же тайтл."""
        nfo = ctx.own_nfo if (ctx.is_child and not ctx.inherits_parent) else ctx.nfo
        if nfo is None or not nfo.anilist_id or not nfo.shikimori_id:
            return False
        return (
            self.index.by_anilist.get(nfo.anilist_id) == series_id
            and self.index.by_mal.get(nfo.shikimori_id) == series_id
        )

    def _title_score(self, ctx: MatchContext, candidate: SeriesCandidate) -> float:
        """Насколько название кандидата похоже на то, что видно у папки."""
        variants = [normalize_title(t) for t in TitleIndex._all_variants(candidate)]
        variants = [v for v in variants if v]
        if not variants:
            return 0.0

        best = 0.0
        for raw in self._probe_titles(ctx):
            norm, _ = normalize_for_match(raw)
            if not norm:
                continue
            best = max(best, max(fuzz.token_sort_ratio(norm, v) for v in variants))
        return best

    def _probe_titles(self, ctx: MatchContext) -> list[str]:
        """Строки, которыми папка сама себя называет."""
        out: list[str] = []
        if ctx.is_child and not ctx.inherits_parent:
            # Своё имя и свой `.nfo`: родительские данные к такой папке не относятся.
            out.append(ctx.own_name or ctx.folder_name)
            out.extend(candidate_titles(ctx.own_nfo, ctx.own_name or ctx.folder_name))
        else:
            out.append(ctx.folder_name)
            out.extend(candidate_titles(ctx.nfo, ctx.folder_name))
            if ctx.own_name:
                out.append(ctx.own_name)
        return [t for t in dict.fromkeys(out) if t]

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
            if any(fuzz.token_sort_ratio(norm, v) >= self.suggest_threshold for v in variants):
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
            # Поиск по префиксу ловит сиквелы, которые не попадают в топ-N нечёткого.
            found.extend(self.index.by_prefix(norm))
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

        # Реклама, промо и клипы отдельной папкой в библиотеке не лежат.
        real = [c for c in candidates if c.type not in SERVICE_TYPES]
        candidates = real or candidates
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
