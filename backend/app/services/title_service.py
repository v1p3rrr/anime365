"""Сборка данных для страницы тайтла."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.db import session_scope
from app.core.events import bus
from app.models.catalog import Episode, Series, Translation, TranslationEmbed
from app.services.anime365 import Anime365Client, Anime365Error
from app.services.coverage import (
    NON_CONTENT_EPISODE_TYPES,
    build_quality_summary,
    build_summary,
    effective_episode_total,
)
from app.services.mapping_info import get_local_coverage, get_local_files, get_title_mappings

log = logging.getLogger(__name__)

# Переводы тайтла обновляем не чаще, чем раз в этот срок: список меняется медленно,
# а запрос тяжёлый (до трёх страниц по тысяче записей).
TRANSLATIONS_TTL = timedelta(hours=6)

# Кэш embed живёт дольше: признак вшитых субтитров и набор ступеней практически
# не меняются. Сами ссылки короткоживущие, но их мы и не кэшируем.
EMBED_TTL = timedelta(days=7)


def _parse_episode_num(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


async def ensure_details(series_id: int) -> Series | None:
    """Догружает описания, ссылки и эпизоды — их нет в списочном ответе каталога."""
    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is not None and series.has_details:
            return series

    async with Anime365Client() as client:
        data = await client.series(series_id)

    if data is None:
        return None

    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is None:
            return None

        series.descriptions = data.get("descriptions") or []
        series.links = data.get("links") or []
        series.has_details = True

        episodes = data.get("episodes") or []
        existing = {
            e.id for e in session.execute(
                select(Episode).where(Episode.series_id == series_id)
            ).scalars()
        }
        for item in episodes:
            if item["id"] in existing:
                continue
            session.add(
                Episode(
                    id=item["id"],
                    series_id=series_id,
                    episode_int=str(item.get("episodeInt") or ""),
                    episode_num=_parse_episode_num(item.get("episodeInt")),
                    episode_full=item.get("episodeFull") or "",
                    episode_title=item.get("episodeTitle") or "",
                    episode_type=item.get("episodeType") or "",
                    first_uploaded=item.get("firstUploadedDateTime"),
                    is_active=bool(item.get("isActive", 1)),
                )
            )

    with session_scope() as session:
        return session.get(Series, series_id)


async def ensure_translations(series_id: int, force: bool = False) -> list[dict[str, Any]]:
    """Загружает переводы тайтла и кладёт в кэш."""
    with session_scope() as session:
        rows = (
            session.execute(select(Translation).where(Translation.series_id == series_id))
            .scalars()
            .all()
        )
        fresh = rows and min(r.synced_at for r in rows) > datetime.utcnow() - TRANSLATIONS_TTL
        if rows and fresh and not force:
            return [_translation_dict(r) for r in rows]

    async with Anime365Client() as client:
        items = await client.translations(series_id)

    now = datetime.utcnow()
    payload = []
    for item in items:
        episode = item.get("episode") or {}
        payload.append(
            {
                "id": item["id"],
                "series_id": series_id,
                "episode_id": item.get("episodeId"),
                "type": item.get("type") or "",
                "type_kind": item.get("typeKind") or "",
                "type_lang": item.get("typeLang") or "",
                "authors_summary": item.get("authorsSummary") or "",
                "authors": item.get("authorsList") or [],
                "quality_type": item.get("qualityType"),
                "title": item.get("title"),
                "width": item.get("width"),
                "height": item.get("height"),
                "duration": _to_float(item.get("duration")),
                "is_active": bool(item.get("isActive", 1)),
                "priority": item.get("priority"),
                "url": item.get("url"),
                "added_at": item.get("addedDateTime"),
                "updated_at": item.get("updatedDateTime"),
                "synced_at": now,
                # Номер серии берём из вложенного объекта: отдельный запрос не нужен.
                "_episode_num": _parse_episode_num(episode.get("episodeInt")),
                "_episode_type": episode.get("episodeType") or "",
            }
        )

    if payload:
        rows_for_db = [{k: v for k, v in p.items() if not k.startswith("_")} for p in payload]
        _bulk_upsert_translations(rows_for_db)

    return payload


# У SQLite есть предел числа переменных в одном запросе (32766 в свежих сборках,
# 999 в старых). У популярного тайтла около 2000 переводов по 19 колонок — это
# 38 тысяч переменных, поэтому вставка обязана идти пачками.
_MAX_SQL_VARIABLES = 900
_TRANSLATION_COLUMNS = 20
_UPSERT_CHUNK = max(1, _MAX_SQL_VARIABLES // _TRANSLATION_COLUMNS)


def _bulk_upsert_translations(rows: list[dict[str, Any]]) -> None:
    with session_scope() as session:
        for start in range(0, len(rows), _UPSERT_CHUNK):
            chunk = rows[start : start + _UPSERT_CHUNK]
            stmt = sqlite_insert(Translation).values(chunk)
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[Translation.id],
                    set_={c: getattr(stmt.excluded, c) for c in chunk[0] if c != "id"},
                )
            )


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _translation_dict(row: Translation) -> dict[str, Any]:
    return {
        "id": row.id,
        "series_id": row.series_id,
        "episode_id": row.episode_id,
        "type": row.type,
        "type_kind": row.type_kind,
        "type_lang": row.type_lang,
        "authors_summary": row.authors_summary,
        "authors": row.authors,
        "quality_type": row.quality_type,
        "width": row.width,
        "height": row.height,
        "duration": row.duration,
        "url": row.url,
        "is_active": row.is_active,
        "_episode_num": None,
        "_episode_type": "",
    }


def _attach_episode_numbers(translations: list[dict], episodes: list[Episode]) -> None:
    """Проставляет номер и тип серии тем переводам, у которых их нет (взяты из кэша БД)."""
    by_id = {e.id: e for e in episodes}
    for item in translations:
        if item.get("_episode_num") is not None:
            continue
        episode = by_id.get(item.get("episode_id"))
        if episode is not None:
            item["_episode_num"] = episode.episode_num
            item["_episode_type"] = episode.episode_type


async def build_title_page(series_id: int, force_refresh: bool = False) -> dict[str, Any] | None:
    series = await ensure_details(series_id)
    if series is None:
        return None

    translations = await ensure_translations(series_id, force=force_refresh)

    with session_scope() as session:
        episodes = (
            session.execute(
                select(Episode)
                .where(Episode.series_id == series_id)
                .order_by(Episode.episode_num, Episode.id)
            )
            .scalars()
            .all()
        )
        series = session.get(Series, series_id)
        titles = dict(series.titles or {})
        all_titles = list(series.all_titles or [])
        descriptions = list(series.descriptions or [])
        links = list(series.links or [])
        genres = [{"id": g.id, "title": g.title} for g in series.genres]
        series_data = {
            "id": series.id,
            "type": series.type,
            "type_title": series.type_title,
            "year": series.year,
            "season": series.season,
            "number_of_episodes": series.number_of_episodes,
            "poster_url": series.poster_url,
            "url": series.url,
            "mal_score": series.mal_score,
            "is_airing": series.is_airing,
        }

    _attach_episode_numbers(translations, episodes)

    # Трейлеры и реклама лежат вперемешку с сериями и имеют собственные номера,
    # поэтому в подсчёт «всего серий» они не входят.
    content_episodes = [e for e in episodes if e.episode_type not in NON_CONTENT_EPISODE_TYPES]
    total = effective_episode_total(series.number_of_episodes, len(content_episodes))

    normalized = [
        {**t, "episode_num": t.get("_episode_num"), "episode_type": t.get("_episode_type")}
        for t in translations
    ]

    # Плашка привязки: пользователь должен видеть не только «смаплено», но и чем
    # именно и на какое название — иначе ошибка вскроется только после загрузки
    # файлов не в ту папку.
    mappings = get_title_mappings(series_id)
    local = get_local_coverage(series_id, total)

    # Что из ролей уже лежит локально по каждой серии — чтобы в списке серий было
    # видно, чего не хватает, и не приходилось качать уже имеющееся.
    have_by_episode: dict[float, list[str]] = {}
    for item in get_local_files(series_id):
        have_by_episode.setdefault(item["episode"], []).append(item["role"])

    # Переводы, сгруппированные по серии: из них строится выбор при загрузке
    # конкретной серии.
    by_episode: dict[float, list[dict[str, Any]]] = {}
    for item in normalized:
        episode_num = item.get("episode_num")
        if episode_num is None or item.get("type_lang") not in ("ru", "en", "ja"):
            continue
        by_episode.setdefault(episode_num, []).append(
            {
                "id": item["id"],
                "lang": item.get("type_lang"),
                "kind": item.get("type_kind"),
                "authors": item.get("authors_summary") or "без автора",
                "height": item.get("height"),
            }
        )

    return {
        "series": series_data,
        "mappings": mappings,
        "local": local,
        "titles": titles,
        "all_titles": all_titles,
        "descriptions": descriptions,
        "links": links,
        "genres": genres,
        "episodes": [
            {
                "id": e.id,
                "episode_int": e.episode_int,
                "episode_num": e.episode_num,
                "episode_full": e.episode_full,
                "episode_title": e.episode_title,
                "episode_type": e.episode_type,
                "is_content": e.episode_type not in NON_CONTENT_EPISODE_TYPES,
                # Что уже есть локально: video, sub_ru, sub_ja, sub_en.
                "have": sorted(set(have_by_episode.get(e.episode_num, []))),
                # Доступные переводы этой серии — для выбора при загрузке.
                "translations": by_episode.get(e.episode_num, []),
            }
            for e in episodes
        ],
        "total_episodes": total,
        "summary": build_summary(normalized, total),
        # Точные ступени берём из кэша embed: если пользователь уже нажимал
        # «уточнить», сводка обязана показывать факт, а не прежнюю прикидку.
        "quality": build_quality_summary(normalized, total, exact_heights_for_series(series_id)),
    }


async def get_embed(translation_id: int, force: bool = False) -> dict[str, Any]:
    """Ленивая загрузка embed с кэшированием признаков.

    Кэшируем только устойчивое: вшиты ли субтитры и какие ступени доступны.
    Сами ссылки короткоживущие и подписанные, поэтому воркер загрузки запрашивает
    embed заново непосредственно перед скачиванием.
    """
    if not force:
        with session_scope() as session:
            cached = session.get(TranslationEmbed, translation_id)
            if cached is not None and cached.fetched_at > datetime.utcnow() - EMBED_TTL:
                return {
                    "translation_id": translation_id,
                    "has_subtitles": cached.has_subtitles,
                    "heights": cached.download_heights,
                    "from_cache": True,
                }

    async with Anime365Client() as client:
        info = await client.embed(translation_id)

    with session_scope() as session:
        session.merge(
            TranslationEmbed(
                translation_id=translation_id,
                has_subtitles=info.subtitles_url is not None,
                subtitles_url=info.subtitles_url,
                download_heights=info.heights,
                fetched_at=datetime.utcnow(),
            )
        )

    return {
        "translation_id": translation_id,
        "has_subtitles": info.subtitles_url is not None,
        "heights": info.heights,
        "from_cache": False,
    }


class QualityRefiner:
    """Уточнение качества: дотягивает embed по каждой серии тайтла.

    Оценка в сводке строится по `height` исходника и врёт в обе стороны: у
    источника 528p ступени бывают [720, 360], а у 4K потолок всё равно 1080.
    Точный набор ступеней есть только в embed, а это ОДИН запрос на перевод.

    Поэтому здесь берётся по одному представителю на серию — перевод с
    наибольшим исходником, — и уточняются только они. Для тайтла из 28 серий
    это 28 запросов вместо двух тысяч.
    """

    def __init__(self) -> None:
        self._running: set[int] = set()

    def is_running(self, series_id: int) -> bool:
        return series_id in self._running

    async def run(self, series_id: int) -> dict[str, Any]:
        if series_id in self._running:
            raise RuntimeError("Уточнение уже идёт для этого тайтла")

        self._running.add(series_id)
        try:
            return await self._refine(series_id)
        finally:
            self._running.discard(series_id)

    async def _refine(self, series_id: int) -> dict[str, Any]:
        representatives = self._pick_representatives(series_id)
        total = len(representatives)
        done = failed = 0
        last_error: str | None = None

        bus.publish("quality_refine", series_id=series_id, done=0, total=total, status="running")

        for episode, translation_id in representatives.items():
            try:
                await get_embed(translation_id)
            except Anime365Error as exc:
                # Один недоступный перевод не должен прекращать уточнение:
                # остальные серии всё равно станут точными.
                failed += 1
                last_error = str(exc)
                log.warning("Не удалось уточнить серию %s тайтла %s: %s", episode, series_id, exc)
            done += 1
            bus.publish(
                "quality_refine", series_id=series_id, done=done, total=total, status="running"
            )

        bus.publish(
            "quality_refine",
            series_id=series_id,
            done=done,
            total=total,
            failed=failed,
            status="done",
        )
        # Когда не удалась НИ ОДНА серия, причина почти всегда общая — чаще
        # всего токен: embed без него отвечает 403. Сообщить «не удалось 28»
        # без причины означает отправить пользователя гадать.
        reason = last_error if failed and failed == total else None
        return {
            "episodes": total,
            "refined": done - failed,
            "failed": failed,
            "reason": reason,
        }

    @staticmethod
    def _pick_representatives(series_id: int) -> dict[float, int]:
        """По одному переводу на серию — с максимальным исходником.

        Ступени скачивания у разных переводов одной серии различаются, и нас
        интересует лучшее из доступного, а не среднее.
        """
        with session_scope() as session:
            rows = session.execute(
                select(Translation.id, Translation.episode_id, Translation.height)
                .where(Translation.series_id == series_id, Translation.is_active.is_(True))
            ).all()
            episodes = {
                e.id: e.episode_num
                for e in session.execute(
                    select(Episode).where(
                        Episode.series_id == series_id,
                        Episode.episode_type.not_in(NON_CONTENT_EPISODE_TYPES),
                    )
                ).scalars()
            }

        best: dict[float, tuple[int, int]] = {}
        for translation_id, episode_id, height in rows:
            episode_num = episodes.get(episode_id)
            if episode_num is None:
                continue
            current = best.get(episode_num)
            if current is None or (height or 0) > current[1]:
                best[episode_num] = (translation_id, height or 0)

        return {episode: pair[0] for episode, pair in sorted(best.items())}


quality_refiner = QualityRefiner()


def exact_heights_for_series(series_id: int) -> dict[float, list[int]]:
    """Точные ступени по сериям — из кэша embed, без единого запроса в сеть."""
    with session_scope() as session:
        rows = session.execute(
            select(Translation.episode_id, TranslationEmbed.download_heights)
            .join(TranslationEmbed, TranslationEmbed.translation_id == Translation.id)
            .where(Translation.series_id == series_id)
        ).all()
        episodes = {
            e.id: e.episode_num
            for e in session.execute(
                select(Episode).where(Episode.series_id == series_id)
            ).scalars()
        }

    out: dict[float, list[int]] = {}
    for episode_id, heights in rows:
        episode_num = episodes.get(episode_id)
        if episode_num is None or not heights:
            continue
        known = out.setdefault(episode_num, [])
        for height in heights:
            if height not in known:
                known.append(height)
    return {episode: sorted(heights, reverse=True) for episode, heights in out.items()}
