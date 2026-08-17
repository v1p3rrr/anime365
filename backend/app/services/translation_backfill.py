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

from sqlalchemy import select

from app.core.db import session_scope
from app.models.catalog import Translation
from app.models.library import LibraryFile

log = logging.getLogger(__name__)


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


def unknown_translation_ids(limit: int | None = None) -> list[int]:
    """Идентификаторы из имён файлов, которых нет в кэше переводов."""
    cap = max_per_run() if limit is None else limit
    with session_scope() as session:
        rows = session.execute(
            select(LibraryFile.anime365_translation_id)
            .where(LibraryFile.anime365_translation_id.is_not(None))
            .distinct()
        ).scalars()
        wanted = {int(value) for value in rows if value}
        if not wanted:
            return []

        known = set(
            session.execute(
                select(Translation.id).where(Translation.id.in_(wanted))
            ).scalars()
        )

    return sorted(wanted - known)[:cap]


async def resolve_series(translation_ids: list[int]) -> set[int]:
    """Каким тайтлам принадлежат эти переводы. Один запрос на идентификатор."""
    from app.services.anime365 import Anime365Client

    found: set[int] = set()
    if not translation_ids:
        return found

    async with Anime365Client() as client:
        for translation_id in translation_ids:
            try:
                data = await client.translation(translation_id)
            except Exception as exc:  # noqa: BLE001 — сеть, чужой сайт
                log.warning("Досбор: перевод %s не ответил: %s", translation_id, exc)
                continue
            series_id = (data or {}).get("seriesId")
            if series_id:
                found.add(int(series_id))
            else:
                # Перевод удалён с сайта. Файл останется привязанным по папке —
                # это не ошибка, просто идентификатор больше ни на что не ведёт.
                log.info("Досбор: у перевода %s нет тайтла", translation_id)
    return found


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
        return {"unknown": 0, "series": set(), "loaded": 0}

    log.info("Досбор переводов: неизвестных идентификаторов %d", len(wanted))
    series_ids = await resolve_series(wanted)
    loaded = await load_series(series_ids)
    log.info(
        "Досбор переводов: тайтлов найдено %d, загружено %d", len(series_ids), len(loaded)
    )
    return {"unknown": len(wanted), "series": loaded, "loaded": len(loaded)}


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
    return {"unknown": 0, "series": set(), "loaded": 0, "skipped": "event loop"}
