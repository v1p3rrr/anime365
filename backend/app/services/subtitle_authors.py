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

import re
from datetime import datetime

from sqlalchemy import select

from app.core.db import session_scope
from app.models.autodownload import AUTO_LANGS, SubtitleAuthor

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


def suggest_authors(lang: str, query: str | None = None, limit: int = 200) -> list[dict]:
    """Подсказка для поля ввода: кого мы уже видели на этом языке.

    Порядок — по частоте: группа, у которой переводов больше, скорее всего и
    нужна. При равенстве — по имени, чтобы список не прыгал между запросами.
    """
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
