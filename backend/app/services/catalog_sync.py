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
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
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


def _row_from_api(item: dict[str, Any]) -> dict[str, Any]:
    titles = item.get("titles") or {}
    return {
        "id": item["id"],
        "titles": titles,
        "all_titles": item.get("allTitles") or list(titles.values()),
        "title": item.get("title") or "",
        "type": item.get("type") or "",
        "type_title": item.get("typeTitle") or "",
        "year": item.get("year") or None,
        "season": item.get("season") or None,
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


def _upsert_series(session: Session, items: list[dict[str, Any]]) -> int:
    """Пакетная вставка с обновлением. Детальные поля (descriptions/links/episodes)
    намеренно не трогаем: в списочном ответе их нет, и затирать уже загруженные не нужно."""
    if not items:
        return 0

    rows = [_row_from_api(i) for i in items]
    stmt = sqlite_insert(Series).values(rows)
    update_cols = {
        c: getattr(stmt.excluded, c)
        for c in rows[0]
        if c not in ("id", "descriptions", "links", "has_details")
    }
    session.execute(stmt.on_conflict_do_update(index_elements=[Series.id], set_=update_cols))

    _sync_genres(session, items)
    return len(rows)


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

    async def _do_full(self) -> dict[str, Any]:
        total = 0
        touched: list[int] = []
        async with Anime365Client() as client:
            async for batch in client.iter_catalog(after_id=0):
                with session_scope() as session:
                    total += _upsert_series(session, batch)
                touched.extend(i["id"] for i in batch)

        rebuild_fts_for(touched)
        return {"synced": total}

    async def _do_incremental(self) -> dict[str, Any]:
        with session_scope() as session:
            max_id = session.execute(select(Series.id).order_by(Series.id.desc()).limit(1)).scalar()

        if max_id is None:
            log.info("Каталог пуст — запускаю полную синхронизацию вместо инкрементальной")
            return await self._do_full()

        total = 0
        touched: list[int] = []
        async with Anime365Client() as client:
            async for batch in client.iter_catalog(after_id=max_id):
                with session_scope() as session:
                    total += _upsert_series(session, batch)
                touched.extend(i["id"] for i in batch)

        rebuild_fts_for(touched)
        return {"new": total, "after_id": max_id}

    async def _do_airing(self) -> dict[str, Any]:
        async with Anime365Client() as client:
            items = await client.airing()

        with session_scope() as session:
            count = _upsert_series(session, items)

        rebuild_fts_for([i["id"] for i in items])
        return {"airing": count}


catalog_sync = CatalogSync()
