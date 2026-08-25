"""Каталог групп перевода: нормализация имён и накопление встреченного.

Зачем нормализация. `authorsSummary` приходит с сайта как есть, и одна и та же
группа встречается в нескольких написаниях: `Crunchyroll`, `Crunchyroll [10bit]`,
`Crunchyroll (1080p)`. Без приведения к общему виду в списке приоритетов
пришлось бы перечислять все варианты, а промахнувшись мимо одного — молча не
скачать субтитры.

⚠️ **Скобки выбрасываются целиком** — и круглые, и квадратные. Раньше было не
так: считалось, что `YakuSub Studio (Aero)` — это отдельная команда, и склеивать
её с `YakuSub Studio` нельзя. Решение пересмотрено пользователем
([D-172](../../../claude/09-decisions.md)), и вот с какими доводами.

Каталог существует ровно для одного — списка приоритетов автоскачивания
**субтитров**. В скобках же лежат либо люди внутри группы, либо пометка о файле:
`AniDUB (BalFor & Trina_D)`, `SoundSub (Invento & Killami)`, `AniRise
(перезалив)`, `AniRise (исправленная)`. Выбирать поимённо переводчика внутри
студии никто не собирается, а вот промахнуться мимо одного из четырнадцати
написаний `YakuSub Studio` — легко, и цена промаха: субтитры молча не скачались.

Замерено на живых данных: у субтитров на языках автоскачивания 665 написаний и
630 ключей; правило сводит их к **556**, дополнительно сливая 32 группы. Случаев,
где скобки — это всё имя целиком, не нашлось ни одного, но запасной вариант на
них есть: пустого ключа такая строка не получит.

Что по-прежнему выбрасывается вне скобок: технический хвост — разрешение,
битность, кодек, источник, число каналов (`SubsPlease 1080p`, `Crunchyroll 4k`).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy import select

from app.core.db import session_scope
from app.models.autodownload import (
    AUTO_LANGS,
    AutoDownloadRule,
    SeriesAutoDownload,
    SubtitleAuthor,
)

log = logging.getLogger(__name__)

#: Технические слова, которые описывают файл, а не того, кто его сделал.
#:
#: Список закрытый и намеренно скучный: всё, чего в нём нет, считается частью
#: имени. Ошибиться в эту сторону дешевле — лишняя группа в подсказке заметна,
#: а вот склеенные в одну две разные команды не заметны вовсе.
_TECH_WORDS = {
    # источник
    "bd", "bdrip", "bluray", "blu-ray", "dvd", "dvdrip", "tv", "tvrip",
    "web", "webrip", "webdl", "web-dl", "hdtv", "raw",
    # разрешение и его словесные обозначения
    "hd", "fullhd", "fhd", "uhd", "sd", "4k", "2k",
    # кодеки и звук
    "x264", "x265", "h264", "h265", "hevc", "avc", "aac", "flac", "opus",
    "ac3", "eac3", "eac-3", "dts", "mp3", "vorbis",
    # прочее про картинку
    "hi10p", "hi10", "10bit", "8bit", "12bit",
}

#: Числовые формы того же самого: `1080`, `720p`, `2.0`, `5.1`, `10bit`.
_TECH_PATTERNS = (
    re.compile(r"^\d{3,4}p?$"),
    re.compile(r"^\d+\.\d+$"),
    re.compile(r"^\d+bit$"),
)


def _is_technical(token: str) -> bool:
    cleaned = token.strip().strip(".,;").casefold()
    if not cleaned:
        return True
    if cleaned in _TECH_WORDS:
        return True
    return any(pattern.match(cleaned) for pattern in _TECH_PATTERNS)


#: Скобки любого вида вместе с содержимым. Вложенных в живых данных нет, поэтому
#: выражение простое и не пытается их разбирать.
#:
#: ⚠️ Последние две ветки — НЕЗАКРЫТАЯ скобка до конца строки. Такое приходит с
#: сайта: в каталоге пользователя нашлась группа `Watson (fixed` — без парной
#: скобки, и из-за неё она жила отдельной записью от `Watson`. Хвост после
#: открывающей скобки именем не бывает.
_BRACKETS = re.compile(r"\[[^\[\]]*\]|\([^()]*\)|\([^()]*$|\[[^\[\]]*$")


def _strip_bracket_groups(value: str) -> str:
    """Убирает всё, что в скобках, — и круглых, и квадратных.

    Разбирать содержимое больше не нужно: что там окажется — люди внутри студии
    (`SoundSub (Invento & Killami)`), пометка о файле (`(BD)`) или о самой
    работе (`(перезалив)`, `(исправленная)`) — для списка приоритетов
    безразлично. Выбирают студию, а не переводчика внутри неё.

    ⚠️ Если в скобках было имя целиком, строка остаётся как есть. Иначе ключ
    получился бы пустым, а пустой ключ означает «здесь не автор» — и запись
    молча выпала бы из каталога. В живых данных таких строк не нашлось ни одной,
    но правило дешевле проверки.
    """
    without = _BRACKETS.sub(" ", value)
    without = re.sub(r"\s+", " ", without).strip(" .,;:-&")
    return without or value


def _meaningful_tokens(name: str) -> list[str]:
    """Слова имени без технического хвоста.

    ⚠️ Порядок операций здесь важен, и на нём уже спотыкались. Технический
    хвост отсекается ДО приведения разделителей к пробелу: иначе `EAC-3 2.0`
    успевает превратиться в четыре токена `eac 3 2 0`, ни один из которых сам
    по себе техническим не выглядит, и мусор остаётся в каталоге как «группа».
    """
    value = _strip_bracket_groups(str(name))
    tokens = [token for token in re.split(r"\s+", value) if token]
    while tokens and _is_technical(tokens[-1]):
        tokens.pop()
    return tokens


def normalize_author(name: str | None) -> str:
    """Ключ, по которому группа узнаётся в списке приоритетов.

    Регистр, разделители и технический хвост не должны создавать двойников:
    `Anime-Zone`, `anime zone` и `Anime Zone [1080p]` — одна и та же команда.

    Пустая строка означает «здесь не автор»: так выходит у `EAC-3 2.0` —
    сайт иногда кладёт в это поле описание дорожки. Такие записи в каталог
    групп не попадают.
    """
    if not name:
        return ""

    value = " ".join(_meaningful_tokens(name))
    # Разделители внутри имени приводим к пробелу: дефис и точка ставятся
    # то так, то эдак, и различать по ним — верный способ развести одну
    # команду на три.
    value = re.sub(r"[._\-–—/\|]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.casefold().strip(" .,;:")


def display_name(name: str | None) -> str:
    """Как показать группу человеку: без технического хвоста, но с регистром."""
    if not name:
        return ""
    shown = " ".join(_meaningful_tokens(name)).strip(" .,;:-")
    return shown


def remember_authors(rows: list[dict]) -> int:
    """Пополняет каталог групп по свежезагруженным переводам.

    Берутся только субтитры: озвучка и `raw` к выбору субтитров отношения не
    имеют, а в подсказке мешали бы. Возвращает число новых записей.

    Вызывается там же, где переводы кладутся в базу, — отдельного обхода
    каталога нет и не планируется: 29 тысяч тайтлов ради списка групп не стоят
    ни времени, ни запросов к чужому API.
    """
    seen: dict[tuple[str, str], tuple[str, int]] = {}
    for row in rows:
        if (row.get("type_kind") or "") != "sub":
            continue
        lang = row.get("type_lang") or ""
        if lang not in AUTO_LANGS:
            continue
        key = normalize_author(row.get("authors_summary"))
        if not key:
            continue
        shown, count = seen.get((lang, key), (display_name(row.get("authors_summary")), 0))
        seen[(lang, key)] = (shown, count + 1)

    if not seen:
        return 0

    added = 0
    now = datetime.utcnow()
    with session_scope() as session:
        existing = {
            (row.lang, row.key): row
            for row in session.execute(
                select(SubtitleAuthor).where(
                    SubtitleAuthor.key.in_({key for _, key in seen})
                )
            ).scalars()
        }
        for (lang, key), (shown, count) in seen.items():
            row = existing.get((lang, key))
            if row is None:
                session.add(
                    SubtitleAuthor(
                        lang=lang, key=key, name=shown[:160], seen=count, last_seen=now
                    )
                )
                added += 1
            else:
                # Счётчик именно перезаписывается, а не накапливается: переводы
                # тайтла грузятся целиком и повторно, и сложение превратило бы
                # «сколько их есть» в «сколько раз мы открывали страницу».
                row.seen = max(row.seen or 0, count)
                row.last_seen = now
    return added


def _has_any(lang: str) -> bool:
    with session_scope() as session:
        return session.execute(
            select(SubtitleAuthor.id).where(SubtitleAuthor.lang == lang).limit(1)
        ).first() is not None


def suggest_authors(lang: str, query: str | None = None, limit: int = 200) -> list[dict]:
    """Подсказка для поля ввода: кого мы уже видели на этом языке.

    Порядок — по частоте: группа, у которой переводов больше, скорее всего и
    нужна. При равенстве — по имени, чтобы список не прыгал между запросами.
    """
    if not _has_any(lang):
        # Каталог пуст, а переводы в базе есть — значит их сохранили раньше,
        # чем появился этот каталог. Дособираем на месте: это дешевле, чем
        # объяснять пользователю, почему подсказка пустая.
        backfill_from_stored_translations()

    with session_scope() as session:
        stmt = select(SubtitleAuthor).where(SubtitleAuthor.lang == lang)
        if query:
            needle = f"%{query.strip().casefold()}%"
            stmt = stmt.where(SubtitleAuthor.key.like(needle))
        stmt = stmt.order_by(SubtitleAuthor.seen.desc(), SubtitleAuthor.name).limit(limit)
        return [
            {"key": row.key, "name": row.name, "seen": row.seen}
            for row in session.execute(stmt).scalars()
        ]


#: Признак того, что запись списка — регулярное выражение, а не имя группы.
#:
#: Хранится приставкой в той же строке, потому что список один: заводить
#: вторую колонку ради двух видов записи значило бы дублировать и порядок,
#: который в этом списке и есть главное.
REGEX_PREFIX = "re:"


def is_regex(entry: str) -> bool:
    return entry.startswith(REGEX_PREFIX)


def regex_pattern(entry: str) -> str:
    return entry[len(REGEX_PREFIX):]


def compile_entry(entry: str) -> "re.Pattern[str] | None":
    """Скомпилированный шаблон записи либо `None`, если это обычное имя."""
    if not is_regex(entry):
        return None
    try:
        return re.compile(regex_pattern(entry), re.IGNORECASE)
    except re.error:
        # Битый шаблон не должен ронять подбор: он просто ни с чем не совпадёт.
        log.warning("Автоскачивание: неверное регулярное выражение %r", entry)
        return None


def entry_matches(entry: str, raw_name: str, normalized_key: str) -> bool:
    """Подходит ли группа под запись списка.

    Обычная запись сверяется с **нормализованным** ключом: так `Crunchyroll` и
    `Crunchyroll [1080p]` остаются одной группой.

    Регулярное выражение проверяется по **обоим** написаниям — исходному и
    нормализованному. Исходное нужно, чтобы можно было зацепиться за то, что
    нормализация выбрасывает (например, за пометку разрешения в скобках), а
    нормализованное — чтобы простой шаблон вроде `crunchy` срабатывал
    независимо от хвоста.
    """
    pattern = compile_entry(entry)
    if pattern is None:
        return entry == normalized_key
    return bool(pattern.search(raw_name or "") or pattern.search(normalized_key or ""))


def _renormalized(entries: list[str]) -> list[str]:
    """Записи списка по текущему правилу, без повторов и с сохранением порядка.

    Порядок — это и есть приоритет, менять его нельзя. Регулярные выражения
    остаются как есть: их пишет человек, и нормализовать шаблон бессмысленно.
    """
    out: list[str] = []
    for entry in entries:
        value = entry if is_regex(entry) else (normalize_author(entry) or entry)
        if value and value not in out:
            out.append(value)
    return out


def renormalize_saved_entries() -> int:
    """Приводит сохранённые списки приоритетов к текущему правилу нормализации.

    ⚠️ Без этого смена правила ломает уже настроенное **молча**. Список хранит
    нормализованный ключ (его кладёт `AuthorPriorityList.tsx`), а обычная запись
    сверяется как `entry == normalized_key`. Стоит правилу измениться — и
    сохранённый `yakusub studio (aero)` перестаёт совпадать с новым ключом
    `yakusub studio`. Внешне ничего не происходит: субтитры просто не качаются.

    Возвращает число изменённых списков. Идемпотентна: повторный прогон по уже
    приведённым записям ничего не меняет.
    """
    changed = 0
    with session_scope() as session:
        for model in (AutoDownloadRule, SeriesAutoDownload):
            for row in session.execute(select(model)).scalars():
                for field in ("priorities", "blacklist"):
                    before = list(getattr(row, field) or [])
                    after = _renormalized(before)
                    if after != before:
                        setattr(row, field, after)
                        changed += 1
    return changed


def recompute_keys() -> int:
    """Пересчитывает ключи каталога и склеивает записи, которые теперь совпали.

    Каталог наполняется лениво и живёт долго, поэтому смена правила
    нормализации оставляет в нём старые ключи навсегда. У пользователя это
    выглядело так: в подсказке шесть отдельных `YakuSub Studio` — с `(Aero)`,
    с тремя разными тройками людей и без скобок вовсе. Замерено: **79 записей
    из 498** со скобками в ключе.

    ⚠️ Порядок операций важен: у таблицы ограничение уникальности на
    `(lang, key)`. Сначала удаляем проигравшие записи и сбрасываем изменения в
    базу, и только потом переписываем ключ победителю — иначе он столкнётся с
    ещё живой строкой, занимающей тот же ключ.

    Победитель — тот, кого встречали чаще: его форма записи и остаётся в
    подсказке. Счётчики складываются, дата последней встречи берётся поздняя.

    Возвращает число склеенных записей. Идемпотентна.
    """
    with session_scope() as session:
        rows = list(session.execute(select(SubtitleAuthor)).scalars())

        groups: dict[tuple[str, str], list[SubtitleAuthor]] = {}
        for row in rows:
            key = normalize_author(row.name)
            if not key:
                # Строка оказалась не автором вовсе (описание дорожки). Трогать
                # её не будем: удаление — не задача пересчёта ключей.
                continue
            groups.setdefault((row.lang, key), []).append(row)

        merged = 0
        plan: list[tuple[SubtitleAuthor, str, int, datetime | None]] = []
        for (_lang, key), members in groups.items():
            members.sort(key=lambda item: (-(item.seen or 0), item.id))
            winner = members[0]
            plan.append(
                (
                    winner,
                    key,
                    sum(item.seen or 0 for item in members),
                    max((item.last_seen for item in members if item.last_seen), default=None),
                )
            )
            for loser in members[1:]:
                session.delete(loser)
                merged += 1

        session.flush()

        for winner, key, seen, last_seen in plan:
            winner.key = key
            winner.name = display_name(winner.name) or winner.name
            winner.seen = seen
            if last_seen is not None:
                winner.last_seen = last_seen

    return merged


def backfill_from_stored_translations(limit: int | None = None) -> int:
    """Наполняет каталог по переводам, уже лежащим в базе.

    ⚠️ Без этого каталог оставался пустым у тех, кто открывал тайтлы **до**
    появления этой возможности. Переводы кэшируются надолго, и повторный заход
    на страницу тайтла берёт их из базы, не обращаясь к API, — а наполнение
    каталога висело только на пути загрузки. Пользователь видел пустую
    подсказку там, где перевод заведомо есть.

    Проход дешёвый и одноразовый по смыслу: он лишь пересчитывает то, что уже
    сохранено, без единого запроса наружу.
    """
    from app.models.catalog import Translation

    with session_scope() as session:
        stmt = select(
            Translation.authors_summary, Translation.type_lang, Translation.type_kind
        ).where(Translation.type_kind == "sub")
        if limit:
            stmt = stmt.limit(limit)
        rows = session.execute(stmt).all()

    payload = [
        {"authors_summary": authors, "type_lang": lang, "type_kind": kind}
        for authors, lang, kind in rows
    ]
    added = remember_authors(payload)
    if added:
        log.info("Каталог групп пополнен из сохранённых переводов: %d записей", added)
    return added
