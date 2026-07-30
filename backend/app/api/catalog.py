"""Эндпоинты каталога.

Пагинация устроена в два шага, потому что виртуальный список на фронте должен сразу
знать полную длину выборки:
  1. `/ids`   — только идентификаторы подходящих тайтлов (29 тысяч id ≈ 200 КБ);
  2. `/cards` — окно карточек по списку id, по мере прокрутки.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import rate_limit
from app.core.db import session_scope
from app.models.catalog import Genre, ScanRun, Series, SeriesGenre
from app.services.anime365 import Anime365Client, Anime365Error
from app.services.catalog_sync import catalog_sync
from app.services.fts import search_ids

log = logging.getLogger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])

MAX_CARDS_PER_REQUEST = 200


class CatalogCard(BaseModel):
    id: int
    title_ru: str | None = None
    title_romaji: str | None = None
    type: str
    type_title: str
    year: int | None = None
    season: str | None = None
    number_of_episodes: int
    poster_url_small: str | None = None
    mal_score: float | None = None
    is_airing: bool


def _card(series: Series) -> CatalogCard:
    titles = series.titles or {}
    return CatalogCard(
        id=series.id,
        title_ru=titles.get("ru"),
        title_romaji=titles.get("romaji") or titles.get("en"),
        type=series.type,
        type_title=series.type_title,
        year=series.year,
        season=series.season,
        number_of_episodes=series.number_of_episodes,
        poster_url_small=series.poster_url_small,
        mal_score=series.mal_score,
        is_airing=series.is_airing,
    )


@router.get("/ids")
def catalog_ids(
    query: Annotated[str | None, Query(description="Поиск по названию (локальный, FTS5)")] = None,
    genres: Annotated[list[int] | None, Query(description="ID жанров")] = None,
    genres_and: Annotated[bool, Query(description="Требовать все жанры, а не любой")] = False,
    type: Annotated[str | None, Query()] = None,
    year_from: Annotated[int | None, Query()] = None,
    year_to: Annotated[int | None, Query()] = None,
    season: Annotated[
        str | None, Query(description="Зима | Весна | Лето | Осень, независимо от года")
    ] = None,
    is_airing: Annotated[bool | None, Query()] = None,
    score_from: Annotated[
        float | None, Query(ge=0, le=10, description="Минимальная оценка MAL")
    ] = None,
    include_hentai: Annotated[bool, Query()] = False,
    sort: Annotated[str, Query(pattern="^(id|year|score|title)$")] = "id",
    sort_dir: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> dict[str, Any]:
    """Идентификаторы выборки целиком — фронт строит по ним виртуальный список."""
    stmt = select(Series.id)

    if not include_hentai:
        stmt = stmt.where(Series.is_hentai.is_(False))
    if type:
        stmt = stmt.where(Series.type == type)
    if year_from is not None:
        stmt = stmt.where(Series.year >= year_from)
    if year_to is not None:
        stmt = stmt.where(Series.year <= year_to)
    if season:
        stmt = stmt.where(Series.season_name == season)
    if is_airing is not None:
        stmt = stmt.where(Series.is_airing.is_(is_airing))
    if score_from is not None:
        # Тайтлы без оценки при фильтре по минимальному баллу не показываем:
        # «оценка от 7» не должно означать «и ещё десять тысяч неоценённых».
        stmt = stmt.where(Series.mal_score.is_not(None), Series.mal_score >= score_from)

    if genres:
        if genres_and:
            # Все указанные жанры сразу: группируем и сверяем количество совпадений.
            sub = (
                select(SeriesGenre.series_id)
                .where(SeriesGenre.genre_id.in_(genres))
                .group_by(SeriesGenre.series_id)
                .having(func.count(func.distinct(SeriesGenre.genre_id)) == len(genres))
            )
        else:
            sub = select(SeriesGenre.series_id).where(SeriesGenre.genre_id.in_(genres))
        stmt = stmt.where(Series.id.in_(sub))

    if query:
        matched = search_ids(query, limit=5000)
        if not matched:
            return {"ids": [], "total": 0}
        stmt = stmt.where(Series.id.in_(matched))

    # Пустые значения всегда в конце, независимо от направления: тайтлы без года
    # или без оценки не должны занимать первый экран ни при какой сортировке.
    descending = sort_dir == "desc"
    columns = {"year": Series.year, "score": Series.mal_score, "title": Series.title}
    column = columns.get(sort)

    if column is None:
        stmt = stmt.order_by(Series.id.desc() if descending else Series.id.asc())
    else:
        order = column.desc() if descending else column.asc()
        # id вторым ключом даёт стабильный порядок при равных значениях.
        stmt = stmt.order_by(order.nullslast(), Series.id.desc())

    with session_scope() as session:
        ids = [row[0] for row in session.execute(stmt).all()]

    return {"ids": ids, "total": len(ids)}


@router.post("/cards")
def catalog_cards(ids: Annotated[list[int], Body(embed=True)]) -> list[CatalogCard]:
    """Окно карточек. Порядок сохраняется тем, который прислал фронт."""
    if not ids:
        return []
    if len(ids) > MAX_CARDS_PER_REQUEST:
        raise HTTPException(400, f"За раз не более {MAX_CARDS_PER_REQUEST} карточек")

    with session_scope() as session:
        rows = session.execute(select(Series).where(Series.id.in_(ids))).scalars().all()
        by_id = {s.id: _card(s) for s in rows}

    return [by_id[i] for i in ids if i in by_id]


@router.get("/genres")
def catalog_genres() -> list[dict[str, Any]]:
    """Жанры со счётчиками. Их всего 74, поэтому фильтрация идёт локально."""
    with session_scope() as session:
        rows = session.execute(
            select(Genre.id, Genre.title, func.count(SeriesGenre.series_id))
            .join(SeriesGenre, SeriesGenre.genre_id == Genre.id, isouter=True)
            .group_by(Genre.id)
            .order_by(func.count(SeriesGenre.series_id).desc())
        ).all()
    return [{"id": gid, "title": title, "count": count} for gid, title, count in rows]


@router.get(
    "/search-online",
    # Каждый вызов — запрос на сайт. Локальный поиск по кэшу лимита не имеет,
    # он не выходит в сеть; ограничиваем только онлайновый.
    dependencies=[Depends(rate_limit("search-online", limit=30, window=60))],
)
async def search_online(query: Annotated[str, Query(min_length=1, max_length=200)]) -> list[dict]:
    """Поиск прямым запросом на сайт — ловит то, чего ещё нет в локальном кэше."""
    try:
        async with Anime365Client() as client:
            found = await client.search(query, limit=50)
    except Anime365Error as exc:
        raise HTTPException(502, f"Anime365 недоступен: {exc}") from exc

    return [
        {
            "id": item.get("id"),
            "title_ru": (item.get("titles") or {}).get("ru"),
            "title_romaji": (item.get("titles") or {}).get("romaji"),
            "type": item.get("type"),
            "year": item.get("year"),
            "poster_url_small": item.get("posterUrlSmall"),
        }
        for item in found
    ]


class SyncRequest(BaseModel):
    mode: str = Field("incremental", pattern="^(full|incremental|airing)$")


@router.post("/sync")
async def trigger_sync(payload: SyncRequest) -> dict[str, Any]:
    if catalog_sync.running:
        raise HTTPException(409, f"Синхронизация уже идёт: {catalog_sync.running}")

    runner = {
        "full": catalog_sync.full,
        "incremental": catalog_sync.incremental,
        "airing": catalog_sync.airing,
    }[payload.mode]

    try:
        stats = await runner()
    except Anime365Error as exc:
        raise HTTPException(502, f"Anime365 недоступен: {exc}") from exc

    return {"mode": payload.mode, "stats": stats}


@router.get("/status")
def catalog_status() -> dict[str, Any]:
    """Состояние кэша и последних синхронизаций — для индикатора в UI."""
    with session_scope() as session:
        total = session.execute(select(func.count(Series.id))).scalar() or 0
        last_runs = (
            session.execute(
                select(ScanRun)
                .where(ScanRun.kind.like("catalog%"))
                .order_by(ScanRun.started_at.desc())
                .limit(5)
            )
            .scalars()
            .all()
        )

    return {
        "total_series": total,
        "running": catalog_sync.running,
        "recent": [
            {
                "kind": r.kind,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "stats": r.stats,
                "error": r.error,
            }
            for r in last_runs
        ],
    }
