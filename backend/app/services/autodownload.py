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
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
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


def episodes_with_video(series_id: int) -> dict[float, tuple[int | None, float | None]]:
    """Серии, у которых на диске есть видео: номер → (id файла, время файла).

    Именно по разметке серий, а не по числу файлов в папке: файл может лежать
    в папке и не быть привязан ни к одной серии (мусор, трейлер, склейка), и
    качать субтитры к такому нечему.
    """
    with session_scope() as session:
        rows = session.execute(
            select(EpisodeMapping.episode, EpisodeMapping.file_id, LibraryFile.mtime)
            .join(LibraryFile, EpisodeMapping.file_id == LibraryFile.id, isouter=True)
            .where(
                EpisodeMapping.series_id == series_id,
                EpisodeMapping.role == VIDEO_ROLE,
            )
        ).all()
    return {float(episode): (file_id, mtime) for episode, file_id, mtime in rows}


def _in_automatic_scope(mtime: float | None, resolved: Resolved, now: datetime) -> bool:
    """Попадает ли серия в автоматический проход.

    Требование пользователя, и оно про доверие: включение автоскачивания **не
    должно** задним числом выкачивать субтитры ко всей библиотеке. Поэтому сам
    по себе факт «файл лежит, субтитров нет» права на скачивание не даёт —
    его даёт только появление файла уже при включённом автоскачивании.

    Всё, что лежало раньше, ждёт кнопки «искать по всей библиотеке».

    ⚠️ Здесь могло быть ещё одно правило — «догонять недавно вышедшие серии»,
    но оно снято: определить дату эфира по каждой серии нечем. У Anime365
    `firstUploadedDateTime` — момент попадания файла на сайт (у Yuru Yuri
    2011 года там 2015-й, а у Fate/stay night вовсе заглушки `2000-01-01`),
    у Шикимори поэпизодных дат в API нет, а `<aired>` в `.nfo` есть только
    там, где файл уже разобрал Jellyfin. Правило, работающее у части серий и
    молчащее у остальных, хуже отсутствующего: его поведение не объяснить.
    """
    if mtime is None:
        # Про файл ничего не известно. В автоматический проход не берём:
        # ошибиться здесь в сторону лишней загрузки неприятнее, чем в сторону
        # пропуска, а кнопка никуда не делась.
        return False

    appeared = datetime.utcfromtimestamp(mtime)

    # Файл ещё не отстоялся: может дописываться, а субтитры к нему могли
    # положить руками следом. См. `autodownload_settle_minutes`.
    settle = settings.get_int("autodownload_settle_minutes")
    if settle > 0 and now - appeared < timedelta(minutes=settle):
        return False

    if resolved.enabled_at is not None and appeared < resolved.enabled_at:
        return False

    # Сколько ещё возвращаться к серии, у которой перевода пока нет.
    window = settings.get_int("autodownload_window_days")
    return window <= 0 or now - appeared <= timedelta(days=window)


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


def missing_episodes(
    series_id: int, resolved: Resolved, automatic: bool = False
) -> list[float]:
    """Серии, к которым субтитров этого языка заведомо нет.

    Возвращает только те, у которых есть локальное видео: остальным качать
    нечего по определению.

    `automatic=True` — проход по расписанию: он берёт не всё подряд, а только
    прошедшее правила из `_in_automatic_scope`. Кнопка зовёт без этого флага и
    проверяет всё, что лежит.
    """
    videos = episodes_with_video(series_id)
    if not videos:
        return []

    now = datetime.utcnow()
    sidecars = _sidecar_languages(series_id)
    embedded = (
        {}
        if resolved.ignore_embedded
        else _embedded_languages([fid for fid, _ in videos.values() if fid])
    )

    missing = []
    for episode, (file_id, mtime) in sorted(videos.items()):
        if automatic and not _in_automatic_scope(mtime, resolved, now):
            continue
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


async def plan_for_series(series_id: int, automatic: bool = False) -> list[Choice]:
    """Что нужно скачать этому тайтлу прямо сейчас. Ничего не ставит в очередь."""
    rules = resolve_for_series(series_id)
    if not rules:
        return []

    plan: list[Choice] = []
    for lang, resolved in rules.items():
        missing = missing_episodes(series_id, resolved, automatic)
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


async def run_for_series(
    series_id: int, user_id: int = 1, automatic: bool = False
) -> dict:
    """Считает план и ставит его в очередь загрузок.

    Постановка идёт через общий `enqueue`, а не своим путём: он уже идемпотентен
    по паре «перевод + тип», умеет позицию в очереди и владельца задачи. Своя
    очередь для автоскачивания означала бы вторую правду о том, что качается.
    """
    from app.services.download_queue import QueueError, enqueue

    plan = await plan_for_series(series_id, automatic)
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


def series_with_files() -> list[int]:
    """Тайтлы, у которых есть хоть одна размеченная серия с видео.

    Обходить весь каталог незачем: автоскачивание работает только там, где
    файлы уже лежат, а таких тайтлов сотни, а не двадцать девять тысяч.
    """
    with session_scope() as session:
        rows = session.execute(
            select(EpisodeMapping.series_id)
            .where(EpisodeMapping.role == VIDEO_ROLE)
            .distinct()
        ).scalars()
        return sorted(rows)


async def run_for_all(user_id: int = 1, automatic: bool = False) -> dict:
    """Проход по всей библиотеке.

    Два разных вызывающих, и разница между ними — в `within_days`:

    * кнопка «искать по всей библиотеке» зовёт без ограничения по сроку —
      человек нажал осознанно и ждёт полного результата;
    * расписание зовёт с окном, иначе каждые несколько часов мы спрашивали бы
      чужой сайт о переводах, которых нет и не будет.

    Ошибка на одном тайтле не должна останавливать остальные: сайт может не
    ответить по любому из сотен переводов, и терять из-за этого весь проход
    неправильно.
    """
    from app.services.autodownload_rules import any_language_enabled

    if not any_language_enabled():
        return {"series": 0, "planned": 0, "queued": 0, "skipped": "автоскачивание выключено"}

    planned = queued = touched = 0
    for series_id in series_with_files():
        try:
            result = await run_for_series(series_id, user_id, automatic)
        except Exception:
            log.exception("Автоскачивание: тайтл %s обработать не удалось", series_id)
            continue
        if result["planned"]:
            touched += 1
        planned += result["planned"]
        queued += result["queued"]

    log.info(
        "Автоскачивание: проход завершён — тайтлов с находками %d, выбрано %d, поставлено %d",
        touched,
        planned,
        queued,
    )
    return {"series": touched, "planned": planned, "queued": queued}
