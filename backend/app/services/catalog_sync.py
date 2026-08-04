"""Синхронизация каталога Anime365 в локальную БД.

Три режима с разной ценой:
  * full        — 44 запроса, ~32 секунды, 29 231 тайтл;
  * incremental — только новые тайтлы по afterId, секунды;
  * airing      — 306 онгоингов, секунды.

У тайтлов нет поля updatedDateTime, поэтому правки существующих записей ловятся
только полным рескaном. Отсюда и разделение расписаний: инкремент часто, полный редко.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.models.catalog import Genre, ScanRun, Series, SeriesGenre
from app.services.anime365 import Anime365Client
from app.services.fts import rebuild_fts_for

log = logging.getLogger(__name__)


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    # API отдаёт -1 в worldArtScore/myAnimeListScore, когда оценки нет.
    return None if result < 0 else result


# Сезон приходит одной строкой вида "Осень 2015". Раскладываем на составляющие,
# чтобы можно было фильтровать «все зимние» независимо от года.
_SEASON_NAMES = ("Зима", "Весна", "Лето", "Осень")


def _split_season(value: str | None) -> str | None:
    if not value:
        return None
    first = value.split()[0]
    return first if first in _SEASON_NAMES else None


def is_junk(item: dict[str, Any]) -> bool:
    """Пустые записи каталога.

    В выдаче есть несколько десятков тайтлов вообще без названий: titles пустой,
    allTitles пустой, года нет, эпизодов нет. В UI они всплывали при сортировке по
    названию как «#879». Смысла хранить их нет — отсекаем до записи в БД.
    """
    titles = item.get("titles") or {}
    all_titles = item.get("allTitles") or []
    has_name = any((v or "").strip() for v in titles.values()) or any(
        (t or "").strip() for t in all_titles
    )
    return not has_name


def _display_title(item: dict[str, Any], titles: dict[str, Any], all_titles: list[Any]) -> str:
    """Название для показа и сортировки.

    У части тайтлов поле `title` приходит пустым, хотя названия есть — например у
    id 29237 заполнен только `titles.ja`. Без подстановки такие записи всплывали
    в начале сортировки по названию как «#29237».
    """
    direct = (item.get("title") or "").strip()
    if direct:
        return direct

    for key in ("ru", "romaji", "en", "ja", "short"):
        value = (titles.get(key) or "").strip()
        if value:
            return value

    for value in all_titles:
        if (value or "").strip():
            return str(value).strip()

    return ""


def _row_from_api(item: dict[str, Any]) -> dict[str, Any]:
    titles = item.get("titles") or {}
    all_titles = item.get("allTitles") or list(titles.values())
    return {
        "id": item["id"],
        "titles": titles,
        "all_titles": all_titles,
        "title": _display_title(item, titles, all_titles),
        "type": item.get("type") or "",
        "type_title": item.get("typeTitle") or "",
        "year": item.get("year") or None,
        "season": item.get("season") or None,
        "season_name": _split_season(item.get("season")),
        "number_of_episodes": item.get("numberOfEpisodes") or 0,
        "poster_url": item.get("posterUrl"),
        "poster_url_small": item.get("posterUrlSmall"),
        "url": item.get("url"),
        "mal_id": item.get("myAnimeListId") or None,
        "anilist_id": item.get("anilistId") or None,
        "anidb_id": item.get("aniDbId") or None,
        "mal_score": _to_float(item.get("myAnimeListScore")),
        "is_airing": bool(item.get("isAiring")),
        "is_active": bool(item.get("isActive", 1)),
        "is_hentai": bool(item.get("isHentai")),
        "synced_at": datetime.utcnow(),
    }


def _upsert_series(session: Session, items: list[dict[str, Any]]) -> tuple[int, int]:
    """Пакетная вставка с обновлением. Детальные поля (descriptions/links/episodes)
    намеренно не трогаем: в списочном ответе их нет, и затирать уже загруженные не нужно.

    Возвращает (сохранено, отброшено мусорных).
    """
    if not items:
        return 0, 0

    clean = [i for i in items if not is_junk(i)]
    skipped = len(items) - len(clean)
    if not clean:
        return 0, skipped

    items = clean
    rows = [_row_from_api(i) for i in items]
    stmt = sqlite_insert(Series).values(rows)
    update_cols = {
        c: getattr(stmt.excluded, c)
        for c in rows[0]
        if c not in ("id", "descriptions", "links", "has_details")
    }
    # Строку трогаем, только если она правда изменилась.
    #
    # Суточный рескан перечитывает весь каталог целиком, но меняются в нём
    # единицы записей: у тайтлов нет `updatedDateTime`, поэтому иначе правки не
    # поймать. Без этого условия SQLite переписывал все 29 тысяч строк и все их
    # индексы — 63 секунды удержания writer-lock на ровном месте. Со сторожем
    # неизменившаяся пачка в тысячу тайтлов стоит 5 мс.
    #
    # `IS NOT`, а не `!=`: в SQLite сравнение с NULL через `!=` даёт NULL, то
    # есть «не изменилось», и тайтл, у которого впервые появилась оценка, не
    # записался бы вовсе. `IS NOT` сравнивает с учётом NULL.
    #
    # `synced_at` из сторожа исключён намеренно: он меняется всегда, и с ним
    # условие было бы истинным для каждой строки. Цена — поле означает «когда
    # запись последний раз менялась», а не «когда мы её последний раз видели»;
    # в логике оно не участвует.
    watched = [name for name in update_cols if name != "synced_at"]
    changed = or_(*[getattr(Series, name).is_not(getattr(stmt.excluded, name)) for name in watched])
    session.execute(
        stmt.on_conflict_do_update(index_elements=[Series.id], set_=update_cols, where=changed)
    )

    _sync_genres(session, items)
    return len(rows), skipped


#: Сколько тайтлов писать в одной транзакции.
#:
#: Та же причина, что у `FTS_CHUNK`: дело не в скорости, а в том, надолго ли
#: берётся writer-lock. Пачка API — тысяча тайтлов, и запись такой пачки целиком
#: занимала базу на 2,4 с; при первом наполнении каталога это 29 таких пауз
#: подряд, во время которых у загрузок не проходит ни одна запись прогресса.
SERIES_WRITE_CHUNK = 250

#: Пауза между порциями — чтобы поток не забирал лок обратно мгновенно.
SERIES_WRITE_PAUSE = 0.01


def upsert_series(items: list[dict[str, Any]]) -> tuple[int, int]:
    """Пишет тайтлы порциями, каждая — своя короткая транзакция.

    Разные порции какое-то время находятся в разном состоянии, и это допустимо:
    записи каталога независимы, ни одна не ссылается на другую. Обрыв посередине
    оставит часть непереписанной — следующий проход её доберёт.
    """
    saved = skipped = 0
    for start in range(0, len(items), SERIES_WRITE_CHUNK):
        chunk = items[start : start + SERIES_WRITE_CHUNK]
        with session_scope() as session:
            part, junk = _upsert_series(session, chunk)
        saved += part
        skipped += junk
        if start + SERIES_WRITE_CHUNK < len(items):
            time.sleep(SERIES_WRITE_PAUSE)
    return saved, skipped


def _sync_genres(session: Session, items: list[dict[str, Any]]) -> None:
    genres: dict[int, str] = {}
    pairs: list[dict[str, int]] = []

    for item in items:
        for genre in item.get("genres") or []:
            gid = genre.get("id")
            if gid is None:
                continue
            genres[gid] = genre.get("title") or ""
            pairs.append({"series_id": item["id"], "genre_id": gid})

    if genres:
        session.execute(
            sqlite_insert(Genre)
            .values([{"id": gid, "title": title} for gid, title in genres.items()])
            .on_conflict_do_nothing(index_elements=[Genre.id])
        )

    if pairs:
        # Связи пересобираем целиком: у тайтла могли убрать жанр.
        series_ids = {p["series_id"] for p in pairs}
        session.execute(delete(SeriesGenre).where(SeriesGenre.series_id.in_(series_ids)))
        session.execute(sqlite_insert(SeriesGenre).values(pairs).on_conflict_do_nothing())


CURSOR_KIND = "catalog_cursor"


def _load_cursor() -> int | None:
    """Докуда дошёл последний обход каталога.

    ⚠️ Курсор хранится отдельно и НЕ выводится из `MAX(series.id)`, хотя так и
    было сделано сначала. Это была ошибка, и вот почему: в ту же таблицу пишут
    ещё два источника — синхронизация онгоингов и онлайн-поиск. Оба приносят
    тайтлы с произвольными, в том числе самыми большими, id.

    Проверено на живой базе: на пустом каталоге синк онгоингов дал 306 тайтлов
    с максимальным id 44219, после чего инкремент пошёл с `afterId=44219` и
    забрал 23 записи. Остальные ~28 900 тайтлов, у которых id меньше, он не
    забрал бы уже никогда — только суточный полный рескан всё чинил, и то
    молча, так что снаружи это выглядело бы как «инкремент отработал успешно».

    Поэтому курсор двигают только полный и инкрементальный обходы.
    """
    with session_scope() as session:
        row = session.execute(
            select(ScanRun.stats)
            .where(ScanRun.kind == CURSOR_KIND)
            .order_by(ScanRun.id.desc())
            .limit(1)
        ).scalar()
    if not row:
        return None
    value = row.get("after_id")
    return int(value) if value is not None else None


def _save_cursor(after_id: int) -> None:
    """Запоминает курсор.

    Отдельной записью в истории проходов, а не настройкой: настройки правит
    пользователь, а это внутреннее состояние, которому там не место.
    """
    with session_scope() as session:
        session.add(
            ScanRun(
                kind=CURSOR_KIND,
                status="ok",
                finished_at=datetime.utcnow(),
                stats={"after_id": after_id},
            )
        )
        # Храним только последнюю запись: история курсора никому не нужна, а
        # расти она будет с каждым часовым инкрементом.
        old = session.execute(
            select(ScanRun.id)
            .where(ScanRun.kind == CURSOR_KIND)
            .order_by(ScanRun.id.desc())
            .offset(1)
        ).scalars().all()
        if old:
            session.query(ScanRun).filter(ScanRun.id.in_(old)).delete(synchronize_session=False)


class CatalogSync:
    def __init__(self) -> None:
        self._running: str | None = None

    @property
    def running(self) -> str | None:
        return self._running

    async def full(self) -> dict[str, Any]:
        return await self._run("catalog_full", self._do_full)

    async def incremental(self) -> dict[str, Any]:
        return await self._run("catalog_incr", self._do_incremental)

    async def airing(self) -> dict[str, Any]:
        return await self._run("catalog_airing", self._do_airing)

    # ------------------------------------------------------------------ #

    async def _run(self, kind: str, func) -> dict[str, Any]:
        if self._running:
            raise RuntimeError(f"Синхронизация уже идёт: {self._running}")

        self._running = kind
        with session_scope() as session:
            run = ScanRun(kind=kind, status="running")
            session.add(run)
            session.flush()
            run_id = run.id

        try:
            stats = await func()
            status, error = "ok", None
        except Exception as exc:
            log.exception("Синхронизация %s упала", kind)
            stats, status, error = {}, "failed", str(exc)
            raise
        finally:
            self._running = None
            with session_scope() as session:
                run = session.get(ScanRun, run_id)
                if run is not None:
                    run.status = status
                    run.finished_at = datetime.utcnow()
                    run.stats = stats
                    run.error = error

        log.info("Синхронизация %s завершена: %s", kind, stats)
        return stats


    async def _walk(self, after_id: int) -> tuple[int, int, list[int], int]:
        """Идёт по каталогу от `after_id` до конца. Возвращает счётчики и курсор.

        Курсор — id последнего увиденного тайтла, а не максимальный из
        сохранённых: пропускать «мусорные» записи и потом начинать с них заново
        значило бы топтаться на месте.
        """
        total = skipped = 0
        touched: list[int] = []
        cursor = after_id
        async with Anime365Client() as client:
            async for batch in client.iter_catalog(after_id=after_id):
                saved, junk = upsert_series(batch)
                total += saved
                skipped += junk
                touched.extend(i["id"] for i in batch if not is_junk(i))
                cursor = max(cursor, max(i["id"] for i in batch))
        return total, skipped, touched, cursor

    async def _do_full(self) -> dict[str, Any]:
        total, skipped, touched, cursor = await self._walk(0)
        rebuild_fts_for(touched)
        _save_cursor(cursor)
        return {"synced": total, "skipped_junk": skipped}

    async def _do_incremental(self) -> dict[str, Any]:
        after_id = _load_cursor()

        if after_id is None:
            log.info("Курсора нет — запускаю полную синхронизацию вместо инкрементальной")
            return await self._do_full()

        total, skipped, touched, cursor = await self._walk(after_id)
        rebuild_fts_for(touched)
        _save_cursor(cursor)
        return {"new": total, "skipped_junk": skipped, "after_id": after_id}

    async def _do_airing(self) -> dict[str, Any]:
        async with Anime365Client() as client:
            items = await client.airing()

        count, skipped = upsert_series(items)

        rebuild_fts_for([i["id"] for i in items if not is_junk(i)])
        return {"airing": count, "skipped_junk": skipped}


catalog_sync = CatalogSync()


def cache_series(items: list[dict[str, Any]]) -> set[int]:
    """Кладёт тайтлы из произвольного ответа API в кэш и обновляет поисковый индекс.

    Нужна онлайн-поиску: страница тайтла работает только с локальной БД, поэтому
    найденное на сайте надо сначала закэшировать, иначе клик по результату ведёт
    в никуда.

    Возвращает идентификаторы, которые в кэше уже были, — чтобы UI мог показать,
    что именно нашлось нового.
    """
    if not items:
        return set()

    ids = [i["id"] for i in items if i.get("id") is not None]
    with session_scope() as session:
        known = {
            row[0] for row in session.execute(select(Series.id).where(Series.id.in_(ids))).all()
        }

    upsert_series(items)

    rebuild_fts_for([i["id"] for i in items if not is_junk(i)])
    return known
