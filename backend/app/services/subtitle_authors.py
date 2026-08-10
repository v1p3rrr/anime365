"""Каталог групп перевода: нормализация имён и накопление встреченного.

Зачем нормализация. `authorsSummary` приходит с сайта как есть, и одна и та же
группа встречается в нескольких написаниях: `Crunchyroll`, `Crunchyroll [10bit]`,
`Crunchyroll (1080p)`. Без приведения к общему виду в списке приоритетов
пришлось бы перечислять все варианты, а промахнувшись мимо одного — молча не
скачать субтитры.

Чего нормализация НЕ делает. Скобки не выбрасываются подряд: `YakuSub Studio
(Aero)` — это название команды, а не технический хвост, и склеивать её с
`YakuSub Studio` нельзя. Выбрасывается только то, что заведомо описывает файл,
а не автора: разрешение, битность, кодек, источник, число каналов.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy import select

from app.core.db import session_scope
from app.models.autodownload import AUTO_LANGS, SubtitleAuthor

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


def _strip_technical_groups(value: str) -> str:
    """Убирает скобки, ЦЕЛИКОМ состоящие из технических слов.

    Проверяется всё содержимое, а не отдельные слова: `(Aero)` остаётся, а
    `[BDRip 1080p x265]` уходит. Половинчатое `(Aero 1080p)` тоже остаётся —
    выкусывать часть скобки означало бы гадать, что там имя, а что описание.
    """

    def replace(match: re.Match[str]) -> str:
        inner = match.group(1)
        tokens = re.split(r"[\s,;/|]+", inner)
        return "" if tokens and all(_is_technical(token) for token in tokens) else match.group(0)

    value = re.sub(r"\[([^\[\]]*)\]", replace, value)
    return re.sub(r"\(([^()]*)\)", replace, value)


def _meaningful_tokens(name: str) -> list[str]:
    """Слова имени без технического хвоста.

    ⚠️ Порядок операций здесь важен, и на нём уже спотыкались. Технический
    хвост отсекается ДО приведения разделителей к пробелу: иначе `EAC-3 2.0`
    успевает превратиться в четыре токена `eac 3 2 0`, ни один из которых сам
    по себе техническим не выглядит, и мусор остаётся в каталоге как «группа».
    """
    value = _strip_technical_groups(str(name))
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
