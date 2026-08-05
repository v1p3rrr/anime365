"""Автоскачивание субтитров: что нужно докачать и чей перевод взять.

Порядок рассуждения такой же, как у человека, который делает это руками:

1. какие серии тайтла **уже лежат видеофайлами** — только к ним и качаем;
2. для каких из них субтитров нужного языка **точно нет**;
3. чей перевод взять по списку приоритетов;
4. поставить в общую очередь загрузок.

Шаг 1 — не оптимизация, а требование пользователя: субтитры без видео никому
не нужны, а к тайтлу, добавленному «на будущее», их набежали бы сотни.

⚠️ Шаг 2 отвечает на вопрос «знаем ли мы, что субтитры есть», а не «есть ли
они». Различие принципиальное и задано пользователем: **не знаем — качаем**.
Данные о вшитых дорожках приходят из `.nfo` и ffprobe, покрытие у обоих
частичное, а теги в `.nfo` бывают откровенно неверными (у `Fate HF I`
видеодорожка помечена как итальянская). Промолчать из-за неизвестности значило
бы тихо не скачать там, где всё в порядке.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.core.db import session_scope
from app.models.catalog import Episode, Translation
from app.models.library import EpisodeMapping, LibraryFile
from app.services.autodownload_rules import Resolved, resolve_for_series
from app.services.languages import normalize_lang
from app.services.subtitle_authors import normalize_author

log = logging.getLogger(__name__)

#: Роль файла с видео в разметке серий.
VIDEO_ROLE = "video"


@dataclass(frozen=True)
class Choice:
    """Что и почему решено скачать."""

    episode: float
    lang: str
    translation_id: int
    author: str
    #: `priority` — нашлась группа из списка; `any` — список отключён;
    #: `fallback` — из списка никого, но разрешена любая группа.
    reason: str


def episodes_with_video(series_id: int) -> dict[float, int | None]:
    """Серии, у которых на диске есть видео: номер → id файла.

    Именно по разметке серий, а не по числу файлов в папке: файл может лежать
    в папке и не быть привязан ни к одной серии (мусор, трейлер, склейка), и
    качать субтитры к такому нечему.
    """
    with session_scope() as session:
        rows = session.execute(
            select(EpisodeMapping.episode, EpisodeMapping.file_id).where(
                EpisodeMapping.series_id == series_id,
                EpisodeMapping.role == VIDEO_ROLE,
            )
        ).all()
    return {float(episode): file_id for episode, file_id in rows}


def _sidecar_languages(series_id: int) -> dict[float, set[str]]:
    """Языки субтитров, лежащих отдельными файлами, по сериям.

    Роль `sub_unknown` сюда не попадает намеренно: язык такого файла нам
    неизвестен, а «неизвестно» по правилу означает «качаем».
    """
    out: dict[float, set[str]] = {}
    with session_scope() as session:
        rows = session.execute(
            select(EpisodeMapping.episode, EpisodeMapping.role).where(
                EpisodeMapping.series_id == series_id,
                EpisodeMapping.role.like("sub\\_%", escape="\\"),
            )
        ).all()
    for episode, role in rows:
        lang = role.removeprefix("sub_")
        if lang and lang != "unknown":
            out.setdefault(float(episode), set()).add(lang)
    return out


def _embedded_languages(file_ids: list[int]) -> dict[int, set[str]]:
    """Языки субтитров внутри контейнера — по данным `.nfo` и ffprobe.

    Отсутствие записи означает «не проверяли», и это не то же самое, что
    «дорожек нет»: см. пояснение в шапке модуля.
    """
    known: dict[int, set[str]] = {}
    if not file_ids:
        return known
    with session_scope() as session:
        rows = session.execute(
            select(LibraryFile.id, LibraryFile.streams).where(LibraryFile.id.in_(file_ids))
        ).all()
    for file_id, streams in rows:
        langs = (streams or {}).get("subtitle_languages") or []
        normalized = {normalize_lang(item) for item in langs}
        known[file_id] = {lang for lang in normalized if lang}
    return known


def missing_episodes(series_id: int, resolved: Resolved) -> list[float]:
    """Серии, к которым субтитров этого языка заведомо нет.

    Возвращает только те, у которых есть локальное видео: остальным качать
    нечего по определению.
    """
    videos = episodes_with_video(series_id)
    if not videos:
        return []

    sidecars = _sidecar_languages(series_id)
    embedded = (
        {}
        if resolved.ignore_embedded
        else _embedded_languages([fid for fid in videos.values() if fid])
    )

    missing = []
    for episode, file_id in sorted(videos.items()):
        if resolved.lang in sidecars.get(episode, set()):
            continue
        if file_id is not None and resolved.lang in embedded.get(file_id, set()):
            continue
        missing.append(episode)
    return missing


def _candidates(series_id: int, lang: str) -> dict[float, list[dict]]:
    """Субтитры этого языка, разложенные по номерам серий.

    Берём из локального кэша переводов: он наполняется при открытии тайтла и
    фоновым обходом, поэтому лишнего запроса к чужому API здесь нет.
    """
    with session_scope() as session:
        rows = session.execute(
            select(
                Translation.id,
                Translation.authors_summary,
                Translation.priority,
                Episode.episode_num,
            )
            .join(Episode, Translation.episode_id == Episode.id)
            .where(
                Translation.series_id == series_id,
                Translation.type_kind == "sub",
                Translation.type_lang == lang,
                Translation.is_active.is_(True),
            )
        ).all()

    out: dict[float, list[dict]] = {}
    for translation_id, authors, priority, episode_num in rows:
        if episode_num is None:
            continue
        out.setdefault(float(episode_num), []).append(
            {
                "id": translation_id,
                "authors": authors or "",
                "key": normalize_author(authors),
                # `priority` у Anime365 — его собственная оценка; при прочих
                # равных доверяем ей, а не случайному порядку строк в базе.
                "priority": priority if priority is not None else -1,
            }
        )
    return out


def rank_candidates(candidates: list[dict], resolved: Resolved) -> list[dict]:
    """Порядок, в котором стоит пробовать переводы.

    Два переключателя, и они разные — это уточнение пользователя:

    * `allow_any` — список **не используется вовсе**, годится любая группа.
      Значения списка при этом сохраняются: переключатель гасит список, а не
      стирает его.
    * `allow_any_fallback` — сначала список, и только если из него никого не
      нашлось, берём остальных.

    Внутри «остальных» порядок — по оценке самого Anime365, затем по номеру
    перевода: без второго ключа выдача прыгала бы между запросами.
    """

    def generic(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda item: (-item["priority"], item["id"]))

    if resolved.allow_any:
        return generic(candidates)

    # Порядок задаёт сам список: перебираем его сверху вниз, а не сортируем
    # кандидатов — так группа, которой в списке нет, не может обогнать ту,
    # что в нём есть, ни по какой другой причине.
    ranked: list[dict] = []
    for key in resolved.priorities:
        matched = [item for item in candidates if item["key"] == key]
        ranked.extend(sorted(matched, key=lambda item: (-item["priority"], item["id"])))

    if not ranked and resolved.allow_any_fallback:
        return generic(candidates)
    return ranked


async def _first_soft_subtitle(candidates: list[dict]) -> dict | None:
    """Первый кандидат, у которого субтитры отдельным файлом.

    ⚠️ Хардсаб отличим только через `embed`, то есть запросом на перевод —
    другого признака в API нет. Поэтому идём по порядку и останавливаемся на
    первом подходящем, а не проверяем всех: у популярного тайтла кандидатов
    бывают десятки, и проверка каждого стоила бы десятков запросов ради
    выбора, который уже сделан.
    """
    from app.services.download_queue import already_downloaded
    from app.services.title_service import get_embed

    for item in candidates:
        if already_downloaded(item["id"]):
            # Этот перевод уже лежит на диске: значит субтитры к серии есть,
            # просто разметка их ещё не увидела. Второй файл не нужен.
            return None
        try:
            info = await get_embed(item["id"])
        except Exception as exc:  # noqa: BLE001 — сеть, чужой сайт
            log.warning("Автоскачивание: embed %s не ответил: %s", item["id"], exc)
            continue
        if info.get("has_subtitles"):
            return item
    return None


async def plan_for_series(series_id: int) -> list[Choice]:
    """Что нужно скачать этому тайтлу прямо сейчас. Ничего не ставит в очередь."""
    rules = resolve_for_series(series_id)
    if not rules:
        return []

    plan: list[Choice] = []
    for lang, resolved in rules.items():
        missing = missing_episodes(series_id, resolved)
        if not missing:
            continue
        by_episode = _candidates(series_id, lang)
        for episode in missing:
            ranked = rank_candidates(by_episode.get(episode, []), resolved)
            if not ranked:
                continue
            chosen = await _first_soft_subtitle(ranked)
            if chosen is None:
                continue
            reason = (
                "any"
                if resolved.allow_any
                else ("priority" if chosen["key"] in resolved.priorities else "fallback")
            )
            plan.append(
                Choice(
                    episode=episode,
                    lang=lang,
                    translation_id=chosen["id"],
                    author=chosen["authors"],
                    reason=reason,
                )
            )
    return plan


async def run_for_series(series_id: int, user_id: int = 1) -> dict:
    """Считает план и ставит его в очередь загрузок.

    Постановка идёт через общий `enqueue`, а не своим путём: он уже идемпотентен
    по паре «перевод + тип», умеет позицию в очереди и владельца задачи. Своя
    очередь для автоскачивания означала бы вторую правду о том, что качается.
    """
    from app.services.download_queue import QueueError, enqueue

    plan = await plan_for_series(series_id)
    queued = 0
    for choice in plan:
        try:
            result = enqueue(choice.translation_id, "subtitles", user_id=user_id)
        except QueueError as exc:
            log.warning(
                "Автоскачивание: перевод %s не поставлен: %s", choice.translation_id, exc
            )
            continue
        if result.get("created"):
            queued += 1

    if plan:
        log.info(
            "Автоскачивание: тайтл %s — выбрано %d, поставлено %d",
            series_id,
            len(plan),
            queued,
        )
    return {"series_id": series_id, "planned": len(plan), "queued": queued}
