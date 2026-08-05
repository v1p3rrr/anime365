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
import re
from dataclasses import dataclass, field
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


#: Явный номер сезона в названии каталога.
#:
#: Формы взяты из настоящих названий: «BanG Dream! 2nd Season», «Season 2»,
#: «Youjo Senki II». Голая цифра в конце сюда намеренно не входит — «Girls Band
#: Cry Movie 2» это часть фильма, а не второй сезон.
_TITLE_SEASON_PATTERNS = (
    re.compile(r"(?i)\b(\d{1,2})(?:st|nd|rd|th)\s+season\b"),
    re.compile(r"(?i)\bseason\s+(\d{1,2})\b"),
    re.compile(r"(?i)\b(\d{1,2})\s*-?\s*й\s+сезон\b"),
)

#: Римские цифры в хвосте названия: «Youjo Senki II».
_TITLE_ROMAN = re.compile(r"(?i)\s(II|III|IV|V)\s*$")
_ROMAN_VALUE = {"ii": 2, "iii": 3, "iv": 4, "v": 5}


def season_in_title(title: str) -> int | None:
    """Номер сезона, **явно названный** в самом заголовке тайтла.

    Нужен там, где число серий бессильно. У папки `Bang Dream!/Season 2` было
    тринадцать файлов, и она уехала на чиби-спиноф `Garupa☆Pico`, вышедший в тот
    же сезон года: дата премьеры совпала, а больше сверять было нечем. Но верный
    ответ называется «BanG Dream! **2nd Season**» — номер написан прямо в нём.

    Сравнение по количеству здесь не помогло бы принципиально: скачано может
    быть десять серий из двенадцати, а у чиби-сериала рядом их двадцать четыре.
    """
    for pattern in _TITLE_SEASON_PATTERNS:
        match = pattern.search(title)
        if match:
            return int(match.group(1))
    match = _TITLE_ROMAN.search(title)
    if match:
        return _ROMAN_VALUE[match.group(1).lower()]
    return None


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

#: Уверенность сиквела, найденного по объявленному в заголовке номеру сезона.
#:
#: Высокая: номер написан в самом названии тайтла, гадать не о чем. Но не сотня
#: — сверка по году, типу и кварталу премьеры всё равно должна иметь право
#: отвергнуть кандидата.
SEQUEL_CONFIDENCE = 94.0

#: Перевес кандидата, чей заголовок объявляет тот же номер сезона.
#:
#: Величина посчитана, а не подобрана. У папки второго сезона имя дословно
#: совпадает с **первым** сезоном («Kagerou Days» против «Kagerou Days 2nd
#: Season»), и первый выигрывает дважды: точным совпадением названия
#: (99 против 94 стартовых) и похожестью — до `(100 - TITLE_FLOOR) *
#: TITLE_WEIGHT` ≈ 19 баллов против примерно девяти у сиквела.
#:
#: Худший случай, который надо перекрыть, — это 5 + 19 ≈ 24. Берём 30: с
#: запасом, но не настолько, чтобы подменить собой год, тип и квартал премьеры —
#: они по-прежнему могут отвергнуть кандидата вовсе.
SEASON_CLAIM_BONUS = 30.0

#: На сколько лет год фильма может разойтись с каталогом.
#:
#: Единица, а не ноль: у фильмов с зимней премьерой год выхода и год каталога
#: расходятся законно, а фестивальный показ и прокат бывают в разные годы.
#: Всё, что дальше, — это уже другой фильм.
MOVIE_YEAR_SLACK = 1

#: Типы каталога, которыми бывают спешлы.
#:
#: `ona` здесь есть. Сначала его исключили — так помечают полноценные
#: веб-сериалы (`Mini Yuri`, четыре серии), и он конкурировал бы со спешлом за
#: ту же папку. Но тип оказался не тем признаком, на котором это должно
#: держаться: разводит кандидатов **название из имени файла**, а по типу мы лишь
#: отсекаем заведомо не то — сам сериал. С названием ограничение можно ослабить,
#: и спиноф, положенный в `Specials`, перестаёт быть невидимым.
SPECIAL_TYPES = frozenset({"special", "tv_special", "ova", "ona"})

#: Насколько название из имени файла должно совпасть с вариантом названия
#: кандидата, чтобы считаться опознанием.
#:
#: ⚠️ Порог низкий намеренно, и это исправление собственной ошибки. Сначала он
#: был 70 — по одному случаю Toradora, где совпадение почти дословное. На втором
#: живом случае это отвергло верный ответ: у файла `YuruYuri … S00E01 - Love
#: Paradox Eternal` против варианта `YuruYuri: For Whatever Reason, Never
#: Ending, … Paradox, Eternal` выходит **64,9**, потому что общее там только
#: хвост названия.
#:
#: Решает не абсолютная величина, а **отрыв от соседей**: у того же файла
#: следующий кандидат набрал 32,8, то есть лидер очевиден. Поэтому порог
#: отсекает лишь совсем случайные совпадения, а выбор делает проверка на
#: единоличность (`AMBIGUITY_MARGIN`) — она была здесь с самого начала.
SPECIAL_TITLE_FLOOR = 50.0

#: Уверенность привязки папки спешлов.
#:
#: Заметно ниже точного совпадения: название папки («Specials») в выборе не
#: участвует вовсе, решает только принадлежность франшизе и тип. Это разумная
#: догадка, а не установленный факт, и плашка на странице тайтла обязана
#: показывать её именно так.
SPECIALS_CONFIDENCE = 70.0

#: Уверенность, когда спешл назван в имени файла и опознан однозначно.
#:
#: Высокая намеренно: это уже не «папка принадлежит франшизе», а совпадение
#: конкретного названия с вариантом названия конкретного вхождения.
SPECIALS_NAMED_CONFIDENCE = 92.0


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
    #: Номер сезона из имени самой папки («Season 2»), когда `season.nfo` нет.
    own_season: int | None = None
    #: Имена видеофайлов папки без расширения.
    #:
    #: Нужны папке спешлов: сама она называется «Specials», а вот в имени файла
    #: стоит название конкретного спешла — `Toradora! S00E05 - The True Meaning
    #: of Bento`. В каталоге у вхождения 9781 среди вариантов названия есть
    #: ровно `Toradora!: The True Meaning of Bento`, так что это опознание,
    #: а не догадка.
    file_names: list[str] = field(default_factory=list)
    #: Номер сезона, на котором сошлись имена видеофайлов папки.
    #:
    #: Заполняется только при единогласии. Реальный случай: в папке
    #: `Classroom of the Elite` лежат 17 файлов `S04E01…`, а подпапок сезонов
    #: нет вовсе — ни `season.nfo`, ни имя папки о четвёртом сезоне не говорят,
    #: и папка уезжала на первый сезон.
    season_from_files: int | None = None

    @property
    def season_number(self) -> int | None:
        if self.season_nfo is not None and self.season_nfo.season is not None:
            return self.season_nfo.season
        if self.own_season is not None:
            return self.own_season
        return self.season_from_files

    @property
    def season_is_guessed(self) -> bool:
        """Номер сезона взят из имён файлов, а не из метаданных.

        Разница существенна для года: у `season.nfo` есть своя дата премьеры, а
        когда сезон вычислен по файлам, год из `tvshow.nfo` относится к первому
        сезону и никакого отношения к четвёртому не имеет. Считать его
        ожидаемым — значит зарубить единственного правильного кандидата.
        """
        if self.season_nfo is not None and self.season_nfo.season is not None:
            return False
        if self.own_season is not None:
            return False
        return self.season_from_files is not None

    @property
    def is_specials(self) -> bool:
        """Папка спешлов: `Specials`, `Extras`, `OVA` — нулевой номер сезона.

        В Anime365 спешлы — **самостоятельные вхождения**, а не часть сериала.
        Проверено запросами: у K-On! их четыре (`Keikaku!`, `Live House!`,
        `Ura-On!!`, `Ura-On!`), у Toradora! — `Bentou no Gokui`, `Recap`, `SOS!`
        и ещё одно. Поэтому такую папку нельзя ни привязывать к сериалу, ни
        считать его сезоном.
        """
        return self.season_number == 0

    @property
    def inherits_parent(self) -> bool:
        """Пользуемся ли мы данными родителя как своими.

        Только сезоны: у подпапки с номером сезона родительские название и ID
        осмысленны. Всё остальное — фильм, спешлы, бонусы, служебные папки —
        обязано опираться на себя.

        ⚠️ Спешлы исключены явно. Формально у них номер сезона есть (ноль), и
        без этой оговорки они выглядели бы обычным сезоном: взяли бы у сериала
        и название, и идентификаторы, и сели бы прямо на него.
        """
        return self.is_child and self.season_number is not None and not self.is_specials


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

        # `.nfo` в этой папке говорит о другом тайтле, чем её имя. Что бы мы ни
        # выбрали, это догадка — и человек должен её увидеть.
        if self.nfo_disagrees_with_folder(ctx):
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

    def nfo_disagrees_with_folder(self, ctx: MatchContext) -> bool:
        """Говорит ли `.nfo` не о том, что написано на папке.

        ⚠️ Найдено на живой библиотеке 2026-08-05, и это оказалось причиной
        «большинство фильмов вообще не впопад»:

            папка «A Silent Voice - The Movie» → <title>Meitantei Conan Magic File 2…
            папка «Look Back»                  → <title>Kekkai Sensen & Beyond…
            папка «Cencoroll Connect»          → <title>Connected…

        Jellyfin записал в папки метаданные **другого аниме**, и матчер честно
        приводил к тому, что там написано. Знать, какое аниме правильное, мы не
        можем и не должны. Но заметить, что два источника в одной папке
        противоречат друг другу, — можем: имя папки человек видит глазами, и
        оно осмысленно.

        Правило работает только там, где имя папки что-то значит: у сезонной
        подпапки оно «Season 2» и сравнивать его не с чем.
        """
        nfo = ctx.own_nfo or ctx.nfo
        name = ctx.own_name or ctx.folder_name
        if nfo is None or not name or ctx.inherits_parent:
            return False

        title = (nfo.title or "").strip()
        if not title:
            return False

        # ⚠️ Сравнивать сами строки бесполезно, и это измерено:
        #
        #     «A Silent Voice - The Movie» против «Meitantei Conan Magic File 2» → 42,3
        #     «Yuru Camp»                  против «Laid-Back Camp»              → 43,5
        #
        # Один балл разницы, а смысл противоположный: первое — метаданные чужого
        # аниме, второе — то же самое аниме под другим названием. Никакой порог
        # их не разведёт.
        #
        # Поэтому сравниваем не строки, а **тайтлы, к которым они приводят**:
        # «Yuru Camp» и «Laid-Back Camp» находят один и тот же тайтл каталога,
        # а «A Silent Voice» и «Meitantei Conan» — разные.
        from_folder = self._resolve_by_string(name)
        from_nfo = self._resolve_by_string(title)
        return from_folder is not None and from_nfo is not None and from_folder != from_nfo

    def _resolve_by_string(self, text: str) -> int | None:
        """К какому тайтлу приводит строка. None — ни к какому уверенно."""
        norm = normalize_title(text)
        if not norm:
            return None

        exact = self.index.by_norm_title.get(norm)
        if exact and len(exact) == 1:
            return exact[0]
        if exact:
            return exact[0]

        found = self.index.fuzzy(norm, limit=1)
        if not found:
            return None
        key, score = found[0]
        if score < self.auto_threshold:
            return None
        ids = self.index.by_norm_title.get(key) or []
        return ids[0] if ids else None

    def _propose(self, ctx: MatchContext) -> list[MatchResult]:
        """Все правдоподобные кандидаты, а не первый попавшийся."""
        out: list[MatchResult] = []
        if ctx.is_specials:
            return self._propose_specials(ctx)
        if not self.nfo_disagrees_with_folder(ctx):
            out.extend(self._propose_ids(ctx))
        out.extend(self._propose_sequels(ctx))
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

    def _propose_sequels(self, ctx: MatchContext) -> list[MatchResult]:
        """Сиквелы, объявившие в заголовке тот самый номер сезона.

        Нужны там, где сезон известен, а `season.nfo` нет: каскад сезонов
        опирается на дату премьеры и без него молчит, а строковый каскад
        предлагает **точное** совпадение — то есть первый сезон, чьё название
        дословно совпадает с именем папки.

        Так папка `Kagerou Days` с файлами `S02E01…` и получала первый сезон:
        второго ей никто не предлагал. Ищем по префиксу названия франшизы и
        берём только тех, кто сам объявил нужный номер, — вольностей здесь нет.
        """
        if ctx.is_specials or ctx.season_number is None or ctx.season_number <= 1:
            return []

        out: list[MatchResult] = []
        seen: set[int] = set()
        for raw in self._match_titles(ctx):
            norm, _ = normalize_for_match(raw)
            if not norm:
                continue
            for series_id in self.index.by_prefix(norm):
                if series_id in seen:
                    continue
                candidate = self.index.by_id.get(series_id)
                if candidate is None:
                    continue
                if self._season_claimed_by(candidate) != ctx.season_number:
                    continue
                seen.add(series_id)
                out.append(
                    MatchResult(
                        series_id,
                        MatchSource.EXACT_TITLE,
                        SEQUEL_CONFIDENCE,
                        f"сезон {ctx.season_number} назван в заголовке",
                    )
                )
        return out

    def _propose_specials(self, ctx: MatchContext) -> list[MatchResult]:
        """Кандидаты для папки спешлов — только вхождения спешл-типов.

        Идентификаторы родителя сюда не идут вовсе: `anilistid` из `tvshow.nfo`
        указывает на сериал, а спешл — самостоятельная запись.

        Ищем по **префиксу** названия франшизы, а не нечётким сравнением:
        спешл называется длиннее сериала («Toradora!» против «Toradora!: SOS!
        Kuishinbou Banbanzai»), и порог похожести его не берёт. Ровно для этого
        `by_prefix` и заведён — он же ловит сиквелы.

        Когда подходящих вхождений несколько (у K-On! их четыре), выбрать по
        имени папки нечего: она называется «Specials». Тогда предложение
        помечается требующим подтверждения — пусть человек посмотрит, а не
        узнаёт об ошибке по уехавшим файлам. Сами файлы при этом не пропадут:
        скачанные нами несут `t{id}` и находят свой тайтл сами.
        """
        found: set[int] = set()
        for raw in self._match_titles(ctx):
            norm, _ = normalize_for_match(raw)
            if not norm:
                continue
            for series_id in self.index.by_prefix(norm):
                candidate = self.index.by_id.get(series_id)
                if candidate is not None and candidate.type in SPECIAL_TYPES:
                    found.add(series_id)

        if not found:
            return []

        # Имя файла называет конкретный спешл. Если оно сошлось с вариантом
        # названия ровно одного кандидата — это опознание, и подтверждать
        # нечего.
        named = self._special_by_file_name(ctx, found)
        if named is not None:
            series_id, matched_on = named
            return [
                MatchResult(
                    series_id,
                    MatchSource.EXACT_TITLE,
                    SPECIALS_NAMED_CONFIDENCE,
                    matched_on,
                )
            ]

        ambiguous = len(found) > 1
        return [
            MatchResult(
                series_id,
                MatchSource.FUZZY,
                SPECIALS_CONFIDENCE,
                f"спешлы франшизы «{ctx.folder_name}»",
                needs_confirmation=ambiguous,
            )
            for series_id in found
        ]

    def _special_by_file_name(
        self, ctx: MatchContext, candidates: set[int]
    ) -> tuple[int, str] | None:
        """Кандидат, чьё название стоит в имени файла.

        Сравниваем `token_set_ratio`, а не посимвольно: в имени файла к названию
        приклеены и код серии, и релиз-группа, и метка качества, а у каталожного
        варианта их нет. У этой меры лишние слова с одной стороны не штрафуются,
        что здесь и нужно.

        Побеждает только **единоличный** лидер: если два спешла набрали столько
        же, имя файла их не развело, и решать за человека нельзя.
        """
        if not ctx.file_names:
            return None

        normalized_files = [normalize_for_match(name)[0] for name in ctx.file_names]
        normalized_files = [name for name in normalized_files if name]
        if not normalized_files:
            return None

        best: tuple[float, int, str] | None = None
        runner_up = 0.0
        for series_id in candidates:
            candidate = self.index.by_id.get(series_id)
            if candidate is None:
                continue
            variants = [normalize_title(t) for t in TitleIndex._all_variants(candidate)]
            score = 0.0
            matched_variant = ""
            for variant in variants:
                if not variant:
                    continue
                for name in normalized_files:
                    hit = fuzz.token_set_ratio(variant, name)
                    if hit > score:
                        score, matched_variant = hit, variant
            if best is None or score > best[0]:
                if best is not None:
                    runner_up = max(runner_up, best[0])
                best = (score, series_id, matched_variant)
            else:
                runner_up = max(runner_up, score)

        if best is None or best[0] < SPECIAL_TITLE_FLOOR:
            return None
        if best[0] - runner_up < AMBIGUITY_MARGIN:
            return None
        return best[1], f"имя файла → «{best[2]}»"

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

        # Папке спешлов подходят только вхождения спешл-типов. Название у них
        # совпадает с сериалом почти дословно («Toradora!» и «Toradora!: SOS!»),
        # поэтому строка сама по себе выбор не делает — делает тип.
        if ctx.is_specials and candidate.type not in SPECIAL_TYPES:
            return None

        # Номер сезона, названный прямо в заголовке кандидата, — самый сильный
        # признак из имеющихся, и он не зависит от того, сколько скачано.
        #
        # У папки `Bang Dream!/Season 2` тринадцать файлов, и она уехала на
        # чиби-спиноф `Garupa☆Pico`, вышедший в тот же сезон года: дата премьеры
        # совпала, сверять было больше нечем. Верный ответ при этом называется
        # «BanG Dream! 2nd Season» — номер написан в нём буквально.
        #
        # Сравнение по количеству серий тут бессильно принципиально: скачано
        # может быть десять из двенадцати, а у чиби-сериала рядом их двадцать
        # четыре. Поэтому решает не счёт, а совпадение явных номеров.
        season_claim_bonus = 0.0
        if not ctx.is_specials and ctx.season_number is not None:
            claimed = self._season_claimed_by(candidate)
            if claimed is not None and claimed != ctx.season_number:
                return None
            # Совпал — это сильнее похожести названия, и надбавка нужна именно
            # поэтому. У папки со вторым сезоном имя обычно совпадает с
            # **первым** сезоном дословно («Kagerou Days» против «Kagerou Days
            # 2nd Season»), так что по строке первый выигрывает всегда.
            #
            # Отвергать кандидата без номера нельзя: сиквелы сплошь называются
            # без него — «Yuru Yuri♪♪», «Yuru Yuri San☆Hai!». Поэтому не вето,
            # а перевес.
            if claimed is not None and claimed == ctx.season_number:
                season_claim_bonus = SEASON_CLAIM_BONUS

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

        # У фильма ровно один год выхода, и это самый твёрдый факт о нём.
        #
        # `Tamako Love Story` (2014) уехал на `True Love Story` (2003): названия
        # похожи, и одного этого хватило. Для сериала расхождение в годах бывает
        # законным — сезоны, переиздания, — а у фильма нет: другой год означает
        # другой фильм. Поэтому здесь не штраф, как у сериалов, а отказ.
        movie_year = self._expected_year(ctx)
        if ctx.scope == "movie" and movie_year and candidate.year:
            if abs(candidate.year - movie_year) > MOVIE_YEAR_SLACK:
                return None

        # Название не сошлось и подтвердить нечем — отказ. Так отсеивается
        # `Your Name` → реклама минеральной воды: идентификатор в .nfo верный,
        # а вот у Anime365 он проставлен чужому тайтлу.
        #
        # ⚠️ К спешлам этот порог неприменим. Их название длиннее названия
        # франшизы на целый хвост («Toradora!» против «Toradora!: SOS!
        # Kuishinbou Banbanzai»), и похожесть выходит 15–27 при пороге 45 —
        # то есть верный кандидат отсеивался бы всегда. Проверка у них другая
        # и уже пройдена: попадание в префикс франшизы плюс спешл-тип.
        if not ctx.is_specials and title < TITLE_FLOOR:
            if not self._ids_agree(ctx, proposal.series_id):
                return None

        score = proposal.confidence + (title - TITLE_FLOOR) * TITLE_WEIGHT + season_claim_bonus
        if ctx.is_specials:
            # Похожесть здесь только упорядочивает своих: чем длиннее лишний
            # хвост, тем ниже. Штрафовать за него ещё и в общем зачёте незачем.
            score = proposal.confidence + title * TITLE_WEIGHT

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
        #
        # ⚠️ Кроме спешла, опознанного по имени файла. У него похожесть с
        # названием франшизы низкая по устройству (сравнивается «Toradora!» с
        # «Toradora!: The True Meaning of Bento»), а опознание при этом точное —
        # совпал конкретный вариант названия конкретного вхождения. Пометка
        # здесь означала бы «проверьте» на ровном месте.
        if title < SUGGEST_TITLE_FLOOR and proposal.source is not MatchSource.EXACT_TITLE:
            result.needs_confirmation = True
        return score, result

    @staticmethod
    def _expected_year(ctx: MatchContext) -> int | None:
        """Год, которому должен соответствовать тайтл.

        У сезонной папки берётся её собственный год — он и разводит сезоны. Год
        из `<premiered>` считается наравне с `<year>`: в `season.nfo` бывает
        только дата.

        ⚠️ Когда номер сезона вычислен по именам файлов, года у нас нет: тот,
        что лежит в `tvshow.nfo`, относится к первому сезону. Выдавать его за
        ожидаемый нельзя — он зарубит единственного верного кандидата.
        """
        if ctx.season_is_guessed:
            return None

        # У сезона со вторым и дальше номером годится только **его собственная**
        # дата. Год из `tvshow.nfo` родителя описывает первый сезон, и подставлять
        # его сюда — значит зарубить верного кандидата: у подпапки
        # `[Ohys-Raws] … 2nd Season` ожидаемым годом оказывался 2019-й, а второй
        # сезон вышел в 2021-м, и проверка отвергала именно его.
        sources: tuple[NfoData | None, ...]
        if ctx.season_number is not None and ctx.season_number > 1:
            sources = (ctx.season_nfo, ctx.own_nfo)
        else:
            sources = (ctx.season_nfo, ctx.own_nfo, ctx.nfo)

        for source in sources:
            if source is None:
                continue
            if source.year:
                return source.year
            quarter = source.season_quarter
            if quarter:
                return quarter[0]
        return None

    @staticmethod
    def _season_claimed_by(candidate: SeriesCandidate) -> int | None:
        """Номер сезона, названный в любом из написаний кандидата.

        Смотрим все варианты, а не одно: «2nd Season» стоит в romaji, а русское
        название той же записи звучит как «2 сезон». Достаточно одного
        написания, чтобы номер считался объявленным.
        """
        for variant in TitleIndex._all_variants(candidate):
            claimed = season_in_title(variant)
            if claimed is not None:
                return claimed
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
        for raw in self._match_titles(ctx):
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
        for raw in self._match_titles(ctx):
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
        for raw in self._match_titles(ctx):
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

    def _match_titles(self, ctx: MatchContext) -> list[str]:
        """Строки, по которым ищем тайтл.

        Обычно это имя папки плюс всё, что написано в её `.nfo`. Но если `.nfo`
        говорит о другом тайтле, чем имя папки, брать оттуда названия нельзя —
        именно так папка «A Silent Voice - The Movie» уехала в «Meitantei Conan
        Magic File 2»: в её `tvshow.nfo` записано именно это название, и
        совпадение вышло **точным**.
        """
        # У папки спешлов собственное имя — «Specials», «Extras», «OVA», — и
        # искать по нему нечего. Опознаётся она по названию франшизы, то есть
        # родителя; отсекает лишнее не строка, а тип кандидата (см. `_verify`).
        if ctx.is_specials:
            return candidate_titles(ctx.nfo, ctx.folder_name)

        if self.nfo_disagrees_with_folder(ctx):
            name = ctx.own_name or ctx.folder_name
            return [name] if name else []
        return candidate_titles(ctx.nfo, ctx.folder_name)

    def _string_candidates(self, ctx: MatchContext) -> list[int]:
        """Идентификаторы тайтлов, похожих по названию, — основа для каскада C."""
        found: list[int] = []
        for raw in self._match_titles(ctx):
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

        # Номер сезона, названный в заголовке. Идёт раньше квартала премьеры,
        # потому что чиби-спиноф выходит **в тот же квартал**, что и настоящий
        # сезон: у `Bang Dream!/Season 2` дата премьеры совпала и с
        # «2nd Season», и с «Garupa☆Pico», и выбор пал на второго.
        #
        # Числом серий это не развести: скачано может быть десять из
        # двенадцати, а у спинофа их двадцать четыре.
        if not ctx.is_specials and ctx.season_number is not None:
            claiming = [
                c for c in candidates if self._season_claimed_by(c) == ctx.season_number
            ]
            if claiming:
                candidates = claiming
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
