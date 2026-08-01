"""Связи между тайтлами — порядок просмотра.

Зачем внешний источник. В API Anime365 связей нет: проверено обходом всех полей
детального ответа — ни `relations`, ни `franchise`, ни чего-либо похожего. Сам
сайт их показывает, но подтягивает со стороны (в блоке описания у них стоит
«© shikimori»). Из-за этого сезоны у нас разводятся по дате премьеры и шаблонам
названий, хотя связь между ними известна.

Почему Шикимори, а не AniList. Сопоставление обратно с нашим каталогом должно
быть точным, а не по названию: у Anime365 есть `mal_id`, и это тот же
идентификатор, которым оперирует Шикимори. Значит связь ложится на наш каталог
без догадок. У AniList свои идентификаторы, и `anilist_id` в каталоге заполнен
реже (19 030 против 28 566).

⚠️ Сопоставление не безупречно: у части тайтлов `mal_id` в каталоге указывает
не туда (у YuruYuri он совпадает с `anilist_id`, что явно ошибка). Поэтому
связь, для которой не нашлось нашего тайтла, показывается всё равно — без
ссылки внутрь, но со ссылкой на Шикимори. Молча выбрасывать её нельзя:
пользователь должен видеть, что продолжение существует, даже если у нас его нет.

Ключ не нужен, `User-Agent` обязателен.

Формат ответа `/api/animes/{id}/franchise` (проверено на живом API):
    {"current_id": 37976,
     "nodes": [{"id", "date", "name", "image_url", "url", "year", "kind", "weight"}],
     "links": [{"source_id", "target_id", "relation", "weight"}]}

Две ловушки в этих данных:
  * `url` относительный (`/animes/50159-...`) — нужен базовый адрес;
  * `kind` здесь **русская подпись** («Фильм», «TV Сериал»), а не код вроде
    `tv`, как в `/related`. Показывать её можно, разбирать — нельзя.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.db import session_scope
from app.models.catalog import Series, SeriesDetails, SeriesRelations

log = logging.getLogger(__name__)

BASE_URL = "https://shikimori.one"

# Связи меняются редко — по сути только когда анонсируют продолжение.
RELATIONS_TTL = timedelta(days=7)

REQUEST_TIMEOUT = 20.0


class ShikimoriError(RuntimeError):
    """Связи получить не удалось. Не повод ломать страницу тайтла."""


async def _fetch(path: str) -> Any:
    # Прокси подхватывается сам: httpx читает HTTPS_PROXY/ALL_PROXY из окружения.
    # Если Шикимори заблокирован, тот же прокси, что и для Anime365, его откроет.
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": settings.get_str("user_agent")},
    ) as client:
        response = await client.get(f"{BASE_URL}{path}", follow_redirects=True)

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise ShikimoriError(f"Шикимори ответил {response.status_code}")
    return response.json()


def _node(item: dict[str, Any]) -> dict[str, Any]:
    url = item.get("url") or ""
    return {
        "mal_id": item.get("id"),
        "name": item.get("name"),
        # Русская подпись вида «TV Сериал» или «Фильм» — показываем как есть.
        "kind": item.get("kind"),
        "year": item.get("year"),
        # В ответе путь относительный, дописываем базовый адрес.
        "shikimori_url": f"{BASE_URL}{url}" if url.startswith("/") else url,
        "image_url": item.get("image_url"),
        # Дата выхода, unix-время. Именно по ней строится порядок.
        "date": item.get("date"),
    }


#: Рёбра, образующие сюжетную линию. Остальные (`summary`, `full_story`,
#: `other`) — рекапы, PV и побочные истории: в линию они не встраиваются.
STORY_RELATIONS = frozenset({"sequel", "prequel"})


def _story_order(nodes: list[dict[str, Any]], links: list[dict[str, Any]]) -> None:
    """Расставляет `order` по сюжету, а не по дате выхода.

    Дата врёт там, где приквел вышел позже: «Магическая битва 0» появилась после
    первого сезона, хотя по сюжету идёт до него. Граф это знает — рёбра
    направленные:

        prequel  Магическая битва   → Магическая битва 0
        sequel   Магическая битва 0 → Магическая битва
        sequel   Магическая битва   → Магическая битва 2

    Поэтому основную линию выстраиваем топологической сортировкой по
    `sequel`/`prequel`, а всё остальное ставим следом по дате. Замерено на
    «Магической битве»: сюжетных рёбер 8 из 24 — то есть линия покрывает лишь
    часть франшизы, и остальное честнее показать отдельной группой, чем
    втискивать в порядок, которого у него нет.
    """
    by_id = {n["mal_id"]: n for n in nodes}

    # Кто вообще участвует в сюжетной линии. У PV, клипов и рекапов сюжетных
    # рёбер нет вовсе, и без этой пометки они всплывали наверх по дате: у
    # «Магической битвы» промо 2018 года оказывалось перед первым сезоном.
    # Место побочного материала — после линии.
    in_story: set[int] = set()
    for link in links:
        if link.get("relation") in STORY_RELATIONS:
            in_story.update((link.get("source_id"), link.get("target_id")))
    for node in nodes:
        node["in_story_line"] = node["mal_id"] in in_story

    # Ребро `sequel` из A в B означает «B — продолжение A», то есть A идёт
    # раньше. `prequel` читается наоборот.
    after: dict[int, set[int]] = {n["mal_id"]: set() for n in nodes}
    incoming: dict[int, int] = {n["mal_id"]: 0 for n in nodes}

    for link in links:
        relation = link.get("relation")
        if relation not in STORY_RELATIONS:
            continue
        src, dst = link.get("source_id"), link.get("target_id")
        if src not in by_id or dst not in by_id:
            continue
        earlier, later = (src, dst) if relation == "sequel" else (dst, src)
        if later in after[earlier]:
            continue
        after[earlier].add(later)
        incoming[later] += 1

    # Обход Кана. Из готовых к выдаче берём самый ранний по дате: франшиза
    # ветвится, и без этого порядок внутри «слоя» был бы случайным.
    ready = sorted(
        (n for n in nodes if incoming[n["mal_id"]] == 0), key=lambda n: n["date"] or 0
    )
    position = 0
    while ready:
        node = ready.pop(0)
        node["order"] = position
        position += 1
        for follower in sorted(after[node["mal_id"]]):
            incoming[follower] -= 1
            if incoming[follower] == 0:
                ready.append(by_id[follower])
        ready.sort(key=lambda n: n["date"] or 0)

    # Циклы в графе франшизы встречаются (взаимные ссылки «продолжение» туда и
    # обратно). Не разобранные узлы просто уходят в конец.
    for node in nodes:
        node.setdefault("order", position)


async def fetch_franchise(mal_id: int) -> dict[str, Any]:
    """Вся линейка тайтла, а не только соседи.

    Берём `/franchise`, а не `/related`: прямые связи дают только соседние
    тайтлы, а нужна вся цепочка — у Re:Zero в ней полтора десятка позиций, и
    прыгать по ним через соседей неудобно.
    """
    data = await _fetch(f"/api/animes/{mal_id}/franchise")
    if not data:
        return {"nodes": [], "current_id": mal_id}

    nodes = [_node(node) for node in data.get("nodes") or []]
    _story_order(nodes, data.get("links") or [])

    # `weight` для сортировки не годится: у Zombieland Saga фильм 2025 года
    # получил вес 1, а исходный сериал 2018-го оказался последним.
    # Сначала сюжетная линия, затем побочное — и то и другое в своём порядке.
    nodes.sort(key=lambda n: (not n.get("in_story_line"), n.get("order", 10**6), n["date"] or 0))
    return {"nodes": nodes, "current_id": data.get("current_id") or mal_id}


def _resolve_to_our_catalog(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Дописывает узлам наш `series_id`, если такой тайтл есть в каталоге."""
    mal_ids = [n["mal_id"] for n in nodes if n.get("mal_id")]
    if not mal_ids:
        return nodes

    with session_scope() as session:
        rows = session.execute(
            select(Series.id, Series.mal_id, Series.titles, Series.poster_url_small).where(
                Series.mal_id.in_(mal_ids)
            )
        ).all()
        by_mal = {mal: (sid, titles, poster) for sid, mal, titles, poster in rows}

    for node in nodes:
        found = by_mal.get(node.get("mal_id"))
        node["series_id"] = found[0] if found else None
        # Своё название показываем в языке интерфейса, поэтому отдаём словарь
        # целиком; у чужих остаётся только имя с Шикимори.
        node["titles"] = found[1] if found else None
        node["poster_url_small"] = found[2] if found else None
    return nodes


async def get_relations(mal_id: int, force: bool = False) -> dict[str, Any]:
    """Связи тайтла с кэшированием.

    Кэш нужен не ради скорости, а ради вежливости к чужому сервису: страницу
    тайтла открывают часто, а связи меняются раз в год.
    """
    if not force:
        with session_scope() as session:
            cached = session.get(SeriesRelations, mal_id)
            if cached is not None and cached.checked_at > datetime.utcnow() - RELATIONS_TTL:
                return {
                    "nodes": _resolve_to_our_catalog(list(cached.nodes or [])),
                    "current_mal_id": mal_id,
                    "from_cache": True,
                }

    data = await fetch_franchise(mal_id)

    with session_scope() as session:
        session.merge(
            SeriesRelations(
                mal_id=mal_id, nodes=data["nodes"], checked_at=datetime.utcnow()
            )
        )

    return {
        "nodes": _resolve_to_our_catalog(data["nodes"]),
        "current_mal_id": data["current_id"],
        "from_cache": False,
    }


# Состояние выхода меняется куда чаще связей: анонс становится онгоингом, а
# онгоинг — вышедшим. Неделя тут была бы слишком грубо.
DETAILS_TTL = timedelta(days=1)


async def fetch_details(mal_id: int) -> dict[str, Any] | None:
    """Студии и состояние выхода — одним запросом `/api/animes/{mal_id}`.

    Anime365 ни того ни другого не отдаёт: в каталоге есть `isAiring`, но он не
    различает «ещё не вышло» и «уже закончилось» — у обоих он ноль.

    Из ответа берём только нужное. Он большой: там же описание, скриншоты,
    ролики, статистика оценок — всё это у нас уже есть из Anime365 или не нужно.
    """
    data = await _fetch(f"/api/animes/{mal_id}")
    if not data:
        return None

    studios = [
        {
            "id": item.get("id"),
            "name": item.get("name") or "",
            # Путь относительный, приписываем адрес — иначе картинка не
            # загрузится, а alt-текста у логотипа нет.
            "image": f"{BASE_URL}{item['image']}" if item.get("image") else None,
        }
        for item in (data.get("studios") or [])
        if item.get("name")
    ]

    return {
        "studios": studios,
        "next_episode_at": data.get("next_episode_at"),
        "status": data.get("status"),
        "aired_on": data.get("aired_on"),
        "released_on": data.get("released_on"),
        "episodes_aired": data.get("episodes_aired"),
        "rating": data.get("rating"),
        "duration": data.get("duration"),
    }


async def get_details(mal_id: int, force: bool = False) -> dict[str, Any] | None:
    """Детали тайтла с кэшированием — как и связи, ради вежливости к источнику.

    Молчаливый отказ здесь намеренный: студия и пометка «анонс» — приятное
    дополнение, а не то, ради чего открывают страницу. Недоступность Шикимори
    не должна её ронять.
    """
    if not force:
        with session_scope() as session:
            cached = session.get(SeriesDetails, mal_id)
            if cached is not None and cached.checked_at > datetime.utcnow() - DETAILS_TTL:
                return {
                    "studios": list(cached.studios or []),
                    "status": cached.status,
                    "aired_on": cached.aired_on,
                    "released_on": cached.released_on,
                    "episodes_aired": cached.episodes_aired,
                    "rating": cached.rating,
                    "duration": cached.duration,
                    "next_episode_at": cached.next_episode_at,
                }

    try:
        data = await fetch_details(mal_id)
    except (ShikimoriError, httpx.HTTPError) as exc:
        log.info("Детали с Шикимори не получены для mal_id=%s: %s", mal_id, exc)
        return None
    if data is None:
        return None

    with session_scope() as session:
        session.merge(SeriesDetails(mal_id=mal_id, checked_at=datetime.utcnow(), **data))
    return data
