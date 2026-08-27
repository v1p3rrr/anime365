"""Досбор переводов, на которые ссылаются имена файлов.

Файл `[Anime365] Sousou no Frieren - 06 (t4505262).mp4` несёт идентификатор
перевода, а перевод в каталоге привязан к тайтлу и эпизоду. Это точная связь, и
она сильнее папки вокруг файла ([D-88](../../../claude/09-decisions.md)).

⚠️ Работала она только наполовину. Связь ищется в **локальном кэше** переводов,
а кэш наполняется лениво — при открытии страницы тайтла. Не открывали страницу
— перевода в базе нет — файл со своим точным идентификатором в имени не
опознаётся ничем, кроме папки. Это записано пределом алгоритма в
`16-mapping-pipeline.md`, и здесь предел снимается.

Три вещи, ради которых тут больше кода, чем кажется нужным:

1. **Ходим по тайтлам, а не по файлам.** Идентификатор перевода даёт `seriesId`
   одним запросом, дальше тайтл догружается целиком. Десять файлов одного
   тайтла стоят двух запросов, а не десяти.
2. **Не держим базу.** Писатель у SQLite один, и сетевой вызов внутри открытой
   транзакции блокирует интерфейс ([D-80](../../../claude/09-decisions.md)).
   Поэтому чтение короткое, сеть — снаружи, запись — короткая.
3. **Переживаем отказ.** Сайт может не ответить по любому идентификатору;
   терять из-за этого весь проход неправильно, а уж тем более — уже сделанное
   сканирование библиотеки.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Translation, TranslationMiss
from app.models.library import LibraryFile

log = logging.getLogger(__name__)

#: Как скоро возвращаться к идентификатору, который не нашёлся.
#:
#: ⚠️ Без этого проход не сходился никогда. Перевод, удалённый с сайта,
#: разрешается в тайтл, тайтл грузится целиком — а самого перевода в его списке
#: нет, и идентификатор остаётся неизвестным. Следующий скан повторял всё с
#: начала; на сервере пользователя это шло каждые две минуты сутками.
RETRY_AFTER = timedelta(hours=24)

#: Через сколько повторять то, что не ответило по сети.
#:
#: ⚠️ Отдельное окно, потому что причины разные по сути, и это записано и в
#: модели `TranslationMiss`, и в [D-156](../../../claude/09-decisions.md):
#: «сайт не знает перевода» и «тайтл ответил, перевода в нём нет» — состояния
#: устойчивые, а отказ сети — случайность.
#:
#: Раньше различие было только на бумаге: отбор смотрел на одну лишь дату
#: попытки, и разовый обрыв связи откладывал идентификатор на **сутки**. Поле
#: `reason` при этом писалось и не читалось ни разу.
#:
#: Час, а не минуты: проход зовётся после каждого сканирования, а сканов при
#: активной загрузке бывает много. Даже при недоступном сайте это не больше
#: одной попытки в час на идентификатор, и все они идут через ограничитель.
ERROR_RETRY_AFTER = timedelta(hours=1)

#: Причина, означающая «попробуем снова скоро».
TEMPORARY_REASON = "error"

#: По сколько идентификаторов подставлять в `IN (…)` — потолок параметров SQLite.
IN_CHUNK = 500


def max_per_run() -> int:
    """Потолок на число неизвестных переводов за один проход.

    Нужен ради первого запуска: у библиотеки, где годами копились чужие
    загрузки, неизвестных идентификаторов могут быть сотни, и превращать
    сканирование в получасовой поход в сеть нельзя. Остаток разберётся
    следующим проходом — файлы никуда не денутся.
    """
    raw = (os.environ.get("A365_BACKFILL_MAX") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return 50


def unknown_ids_in_library() -> set[int]:
    """Все идентификаторы из имён файлов, которых нет в кэше переводов.

    Без потолка и без учёта прошлых попыток — это сырое состояние библиотеки.
    Им же проверяется итог прохода: что после загрузки осталось неизвестным.
    """
    with session_scope() as session:
        rows = session.execute(
            select(LibraryFile.anime365_translation_id)
            .where(LibraryFile.anime365_translation_id.is_not(None))
            .distinct()
        ).scalars()
        wanted = {int(value) for value in rows if value}
        if not wanted:
            return set()

        known: set[int] = set()
        ordered = sorted(wanted)
        for start in range(0, len(ordered), IN_CHUNK):
            known.update(
                session.execute(
                    select(Translation.id).where(
                        Translation.id.in_(ordered[start : start + IN_CHUNK])
                    )
                ).scalars()
            )

    return wanted - known


def unknown_translation_ids(limit: int | None = None) -> list[int]:
    """Кого имеет смысл спросить у сайта в этом проходе.

    Из неизвестных вычитаются те, по кому недавно уже ходили и не нашли
    (`RETRY_AFTER`). Иначе один удалённый с сайта перевод заставлял бы каждый
    скан заново идти в сеть — а сканов при активной загрузке десятки в час.
    """
    cap = max_per_run() if limit is None else limit
    wanted = unknown_ids_in_library()
    if not wanted:
        return []

    now = datetime.utcnow()
    with session_scope() as session:
        # Таблица маленькая (это остатки), поэтому берём её целиком и вычитаем
        # множествами — так не упираемся в потолок параметров запроса. Заодно
        # это позволяет развести окна по причине прямо здесь, не городя
        # условие в SQL.
        rows = session.execute(
            select(
                TranslationMiss.translation_id,
                TranslationMiss.reason,
                TranslationMiss.last_try_at,
            )
        ).all()

    recent = set()
    for translation_id, reason, last_try_at in rows:
        if last_try_at is None:
            # Попытки не было — держать нечего.
            continue
        # ⚠️ Быстрое окно даётся только явному `error`. Всё остальное, включая
        # пустую причину у записей из старых версий, идёт по суточному: иначе
        # мы вернули бы тот самый вечный поход в сеть, ради которого таблица и
        # заведена.
        window = ERROR_RETRY_AFTER if reason == TEMPORARY_REASON else RETRY_AFTER
        if now - last_try_at < window:
            recent.add(translation_id)

    return sorted(wanted - recent)[:cap]


async def resolve_series(translation_ids: list[int]) -> tuple[dict[int, int], dict[int, str]]:
    """Каким тайтлам принадлежат эти переводы. Один запрос на идентификатор.

    Возвращает пару: разрешённые (`перевод → тайтл`) и причины отказа по
    остальным. Причина нужна не для красоты лога, а для решения, стоит ли
    возвращаться: отказ сети — временный, отсутствие перевода на сайте — нет.
    """
    from app.services.anime365 import Anime365Client

    resolved: dict[int, int] = {}
    refused: dict[int, str] = {}
    if not translation_ids:
        return resolved, refused

    async with Anime365Client() as client:
        for translation_id in translation_ids:
            try:
                data = await client.translation(translation_id)
            except Exception as exc:  # noqa: BLE001 — сеть, чужой сайт
                log.warning("Досбор: перевод %s не ответил: %s", translation_id, exc)
                refused[translation_id] = "error"
                continue
            series_id = (data or {}).get("seriesId")
            if series_id:
                resolved[translation_id] = int(series_id)
            else:
                # Перевод удалён с сайта. Файл останется привязанным по папке —
                # это не ошибка, просто идентификатор больше ни на что не ведёт.
                log.info("Досбор: у перевода %s нет тайтла", translation_id)
                refused[translation_id] = "not_found"
    return resolved, refused


def remember_outcome(wanted: list[int], refused: dict[int, str]) -> int:
    """Записывает, по кому ходили впустую, и забывает удачные попытки.

    Считается по факту, а не по намерению: после загрузки тайтлов заново
    смотрим, кто из запрошенных так и не появился в кэше. Тайтл мог ответить
    успешно и всё равно не принести нужный перевод — именно этот случай и
    зацикливал проход.
    """
    if not wanted:
        return 0

    still_unknown = unknown_ids_in_library() & set(wanted)
    now = datetime.utcnow()

    with session_scope() as session:
        found = sorted(set(wanted) - still_unknown)
        for start in range(0, len(found), IN_CHUNK):
            # Нашёлся — значит прошлые неудачи больше ничего не значат.
            session.execute(
                delete(TranslationMiss).where(
                    TranslationMiss.translation_id.in_(found[start : start + IN_CHUNK])
                )
            )

        for translation_id in sorted(still_unknown):
            row = session.get(TranslationMiss, translation_id)
            if row is None:
                row = TranslationMiss(translation_id=translation_id, attempts=0)
                session.add(row)
            row.attempts += 1
            row.last_try_at = now
            # «Тайтл ответил, а перевода в нём нет» — самый частый исход и
            # единственный, который выглядит как успех в логе загрузки.
            row.reason = refused.get(translation_id, "not_in_series")

    return len(still_unknown)


async def load_series(series_ids: set[int]) -> set[int]:
    """Догружает тайтлы целиком: список серий и все переводы."""
    from app.services.title_service import ensure_details, ensure_translations

    loaded: set[int] = set()
    for series_id in sorted(series_ids):
        try:
            await ensure_details(series_id, force=True)
            await ensure_translations(series_id, force=True)
        except Exception:
            log.exception("Досбор: тайтл %s загрузить не удалось", series_id)
            continue
        loaded.add(series_id)
    return loaded


async def backfill() -> dict:
    """Полный проход. Возвращает сводку и множество затронутых тайтлов."""
    wanted = unknown_translation_ids()
    if not wanted:
        return {"unknown": 0, "series": set(), "loaded": 0, "hopeless": 0}

    log.info("Досбор переводов: неизвестных идентификаторов %d", len(wanted))
    resolved, refused = await resolve_series(wanted)
    loaded = await load_series(set(resolved.values()))
    hopeless = remember_outcome(wanted, refused)
    log.info(
        "Досбор переводов: тайтлов найдено %d, загружено %d, осталось неизвестными %d",
        len(set(resolved.values())),
        len(loaded),
        hopeless,
    )
    return {
        "unknown": len(wanted),
        "series": loaded,
        "loaded": len(loaded),
        "hopeless": hopeless,
    }


def backfill_blocking() -> dict:
    """Мостик для синхронного вызывающего — сканирования библиотеки.

    ⚠️ `scan_all` синхронна, и зовут её из двух мест: обработчика запроса (он
    выполняется в пуле потоков, своего цикла событий там нет) и джобы
    планировщика. Внутри работающего цикла `asyncio.run` падает, поэтому
    проверяем явно и в таком случае просто ничего не делаем: досбор — не та
    задача, ради которой стоит рисковать проходом сканирования.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(backfill())

    log.debug("Досбор пропущен: вызван из работающего цикла событий")
    return {"unknown": 0, "series": set(), "loaded": 0, "hopeless": 0, "skipped": "event loop"}
