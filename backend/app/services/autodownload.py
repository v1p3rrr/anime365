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

from sqlalchemy import delete, select

from pathlib import Path

from app.core.config import settings
from app.core.db import session_scope
from app.models.autodownload import AutoDownloadMiss
from app.models.catalog import Episode, Series, Translation
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, LibraryRoot
from app.services.autodownload_rules import Resolved, resolve_for_series
from app.services.coverage import NON_CONTENT_EPISODE_TYPES
from app.services.languages import normalize_lang
from app.services.library_scan import detect_subtitle_language
from app.services.subtitle_authors import entry_matches, normalize_author

log = logging.getLogger(__name__)

#: Роль файла с видео в разметке серий.
VIDEO_ROLE = "video"

#: Расширения, которые считаем субтитрами при проверке на диске.
SUBTITLE_SUFFIXES = frozenset({".ass", ".ssa", ".srt", ".sub", ".vtt"})


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


def _subtitle_language(path: Path) -> str | None:
    """Язык файла субтитров: сначала по имени, затем по содержимому.

    Порядок тот же, что у сканера, и менять его нельзя. Имя надёжнее: суффикс
    `.ru` ставят осознанно. Содержимое — запасной путь для файлов без суффикса,
    и стоит он чтения с диска, поэтому идёт вторым.

    Хвост берётся у основы имени: `Show S01E01.anime365_Kazoku.ru.ass` → `ru`.
    """
    tail = path.stem.rsplit(".", 1)
    if len(tail) == 2:
        found = normalize_lang(tail[1])
        if found:
            return found
    return detect_subtitle_language(path)


def _subtitles_on_disk(file_id: int | None, lang: str) -> tuple[bool, bool]:
    """Смотрит на диск прямо сейчас: `(видео есть, субтитры языка есть)`.

    Индекс библиотеки отражает последний скан, а между сканами файл могли
    удалить или положить рядом субтитры руками. Перед постановкой в очередь
    это дешевле проверить, чем потом разбирать лишнюю загрузку.

    Проверка идёт по соседям видеофайла: Jellyfin подхватывает субтитры только
    при совпадении основы имени, поэтому и наши, и чужие лежат ровно там же.
    """
    if file_id is None:
        return False, False

    with session_scope() as session:
        row = session.execute(
            select(LibraryFile.filename, LibraryFolder.rel_path, LibraryRoot.path)
            .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
            .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
            .where(LibraryFile.id == file_id)
        ).one_or_none()
    if row is None:
        return False, False

    filename, rel_path, root = row
    video = Path(root) / rel_path / filename
    try:
        if not video.is_file():
            return False, False
        stem = video.stem
        for neighbour in video.parent.iterdir():
            if neighbour.suffix.lower() not in SUBTITLE_SUFFIXES:
                continue
            if not neighbour.name.startswith(stem):
                continue
            if _subtitle_language(neighbour) == lang:
                return True, True
    except OSError:
        # Диск недоступен (сеть отвалилась, права). Молча качать в такой
        # ситуации нельзя: мы не знаем, что там лежит.
        return False, False
    return True, False


def _candidates(series_id: int, lang: str) -> dict[float, list[dict]]:
    """Субтитры этого языка, разложенные по номерам серий.

    Берём из локального кэша переводов: он наполняется при открытии тайтла и
    фоновым обходом, поэтому лишнего запроса к чужому API здесь нет.

    ⚠️ **Ключ здесь обязан остаться номером**, в отличие от страницы тайтла
    ([D-170](../../claude/09-decisions.md)). Мы подбираем субтитры к **файлу на
    диске**, а файл назван `S01E01` и о личности серии на сайте не знает: для
    него серия — это номер, другого признака нет.

    Отсюда и опасность. Под одним номером у тайтла живёт несколько разных серий
    (замерено: 48 пар в 42 тайтлах), и в одно ведро попадали переводы трейлера и
    альтернативной нарезки. Дальше побеждал тот, у кого выше `priority`, — то
    есть к 24-минутной серии могли молча приехать субтитры от 12-минутного
    ONA-варианта или вовсе от трейлера, с чужими таймингами и без второй
    половины. Поэтому ведро сужается здесь, до всякого выбора.
    """
    with session_scope() as session:
        series = session.get(Series, series_id)
        series_type = (series.type or "") if series is not None else ""
        rows = session.execute(
            select(
                Translation.id,
                Translation.authors_summary,
                Translation.priority,
                Episode.episode_num,
                Episode.episode_type,
            )
            .join(Episode, Translation.episode_id == Episode.id)
            .where(
                Translation.series_id == series_id,
                Translation.type_kind == "sub",
                Translation.type_lang == lang,
                Translation.is_active.is_(True),
                # Пропавшее с сайта не качаем: ссылки уже нет, попытка кончится
                # отказом и записью о неудаче в очереди.
                Translation.missing_since.is_(None),
            )
        ).all()

    out: dict[float, list[dict]] = {}
    for translation_id, authors, priority, episode_num, episode_type in rows:
        if episode_num is None:
            continue
        # Трейлер под номером первой серии — самый частый случай из замера
        # (28 пар из 48). Его субтитры не подходят к серии никогда.
        if episode_type in NON_CONTENT_EPISODE_TYPES:
            continue
        out.setdefault(float(episode_num), []).append(
            {
                "id": translation_id,
                "authors": authors or "",
                "key": normalize_author(authors),
                # `priority` у Anime365 — его собственная оценка; при прочих
                # равных доверяем ей, а не случайному порядку строк в базе.
                "priority": priority if priority is not None else -1,
                "_episode_type": episode_type or "",
            }
        )

    for number, items in out.items():
        kinds = {item["_episode_type"] for item in items}
        # Под номером осталось несколько ВИДОВ содержимого — например `tv` и
        # `ona`. Канонические серии те, чей вид совпадает с видом самого
        # тайтла: у сериала это телевизионные, у OVA — сами OVA. Если совпадения
        # нет вовсе, ничего не отбрасываем: остаться без кандидатов хуже, чем
        # выбрать из того, что есть.
        if len(kinds) > 1 and series_type in kinds:
            items = [item for item in items if item["_episode_type"] == series_type]
            out[number] = items
        for item in items:
            item.pop("_episode_type", None)

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

    # Чёрный список — раньше всего остального. Запрет обязан быть сильнее
    # любого разрешения: иначе тумблер «любая группа» протащил бы сюда именно
    # то, от чего отписались.
    if resolved.blacklist:
        candidates = [
            item
            for item in candidates
            if not any(
                entry_matches(entry, item["authors"], item["key"])
                for entry in resolved.blacklist
            )
        ]

    if resolved.allow_any:
        return generic(candidates)

    # Порядок задаёт сам список: перебираем его сверху вниз, а не сортируем
    # кандидатов — так группа, которой в списке нет, не может обогнать ту,
    # что в нём есть, ни по какой другой причине.
    ranked: list[dict] = []
    for entry in resolved.priorities:
        matched = [
            item for item in candidates if entry_matches(entry, item["authors"], item["key"])
        ]
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


async def plan_for_series(
    series_id: int, automatic: bool = False
) -> tuple[list[Choice], list[tuple[float, str]]]:
    """Что скачать и к чему не нашлось. Ничего не ставит в очередь.

    Второй список — пары «серия, язык», для которых перебор разрешённых групп
    исчерпан. Туда же попадают случаи, когда все кандидаты оказались хардсабом:
    скачать его нечем, значит найденным он не считается.
    """
    rules = resolve_for_series(series_id)
    if not rules:
        return [], []

    plan: list[Choice] = []
    misses: list[tuple[float, str]] = []
    for lang, resolved in rules.items():
        missing = missing_episodes(series_id, resolved, automatic)
        if not missing:
            continue
        by_episode = _candidates(series_id, lang)
        videos = episodes_with_video(series_id)
        for episode in missing:
            # Последняя проверка — по диску, а не по индексу: файл могли
            # удалить, а субтитры положить руками уже после скана.
            file_id = videos.get(episode, (None, None))[0]
            has_video, has_subs = _subtitles_on_disk(file_id, lang)
            if not has_video or has_subs:
                continue

            ranked = rank_candidates(by_episode.get(episode, []), resolved)
            chosen = await _first_soft_subtitle(ranked) if ranked else None
            if chosen is None:
                misses.append((episode, lang))
                continue
            from_list = any(
                entry_matches(entry, chosen["authors"], chosen["key"])
                for entry in resolved.priorities
            )
            reason = "any" if resolved.allow_any else ("priority" if from_list else "fallback")
            plan.append(
                Choice(
                    episode=episode,
                    lang=lang,
                    translation_id=chosen["id"],
                    author=chosen["authors"],
                    reason=reason,
                )
            )
    return plan, misses


async def run_for_series(
    series_id: int, user_id: int = 1, automatic: bool = False
) -> dict:
    """Считает план и ставит его в очередь загрузок.

    Постановка идёт через общий `enqueue`, а не своим путём: он уже идемпотентен
    по паре «перевод + тип», умеет позицию в очереди и владельца задачи. Своя
    очередь для автоскачивания означала бы вторую правду о том, что качается.
    """
    from app.services.download_queue import QueueError, enqueue

    plan, misses = await plan_for_series(series_id, automatic)
    remember_misses(series_id, misses)
    queued = 0
    for choice in plan:
        try:
            # ⚠️ Тип задачи — «subtitle» в единственном числе, и это не вкус.
            # Воркер узнаёт субтитры строгим равенством, а всё остальное качает
            # как видео. Стоявшее здесь «subtitles» проверку проходило (её не
            # было) и выкачивало видеофайлы целиком: сотни мегабайт вместо
            # десятков килобайт. Набор допустимых значений теперь сторожит
            # сам `enqueue`.
            result = enqueue(choice.translation_id, "subtitle", user_id=user_id)
        except QueueError as exc:
            log.warning(
                "Автоскачивание: перевод %s не поставлен: %s", choice.translation_id, exc
            )
            continue
        if result.get("created"):
            queued += 1
        # Нашли и поставили — запись о промахе больше не нужна.
        clear_miss(series_id, choice.episode, choice.lang)

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


async def run_for_all(
    user_id: int = 1, automatic: bool = False, only_configured: bool = False
) -> dict:
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
    from app.services.autodownload_rules import any_language_enabled, configured_series_ids

    if not any_language_enabled():
        return {"series": 0, "planned": 0, "queued": 0, "skipped": "автоскачивание выключено"}

    # «Только настроенным» — это охват кнопки, а не глобальный режим: человек
    # может держать язык в режиме «всем», но разово пройтись лишь по тем
    # тайтлам, где настроил приоритеты сам.
    configured = set(configured_series_ids()) if only_configured else None

    planned = queued = touched = 0
    for series_id in series_with_files():
        if configured is not None and series_id not in configured:
            continue
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


def remember_misses(series_id: int, misses: list[tuple[float, str]]) -> None:
    """Запоминает серии, к которым субтитров не нашлось.

    Не удалять и заводить заново, а обновлять счётчик: по числу попыток и дате
    первого промаха человек решает, стоит ли ещё ждать перевода или запись
    пора выкинуть.

    ⚠️ Выкинутые вручную (`dismissed`) не воскрешаем. Иначе следующий же
    проход вернул бы в список ровно то, от чего человек только что отписался.
    """
    if not misses:
        return
    now = datetime.utcnow()
    with session_scope() as session:
        for episode, lang in misses:
            row = session.execute(
                select(AutoDownloadMiss).where(
                    AutoDownloadMiss.series_id == series_id,
                    AutoDownloadMiss.episode == episode,
                    AutoDownloadMiss.lang == lang,
                )
            ).scalar_one_or_none()
            if row is None:
                session.add(
                    AutoDownloadMiss(
                        series_id=series_id,
                        episode=episode,
                        lang=lang,
                        first_missed_at=now,
                        last_checked_at=now,
                        checks=1,
                    )
                )
                continue
            if row.dismissed:
                continue
            row.last_checked_at = now
            row.checks = (row.checks or 0) + 1


def clear_miss(series_id: int, episode: float, lang: str) -> None:
    """Перевод нашёлся — запись о промахе больше не нужна."""
    with session_scope() as session:
        session.execute(
            delete(AutoDownloadMiss).where(
                AutoDownloadMiss.series_id == series_id,
                AutoDownloadMiss.episode == episode,
                AutoDownloadMiss.lang == lang,
            )
        )


def pending_misses() -> list[int]:
    """Тайтлы, у которых есть неотброшенные записи о ненайденных субтитрах."""
    with session_scope() as session:
        rows = session.execute(
            select(AutoDownloadMiss.series_id)
            .where(AutoDownloadMiss.dismissed.is_(False))
            .distinct()
        ).scalars()
        return sorted(rows)


async def retry_misses(user_id: int = 1) -> dict:
    """Повторный проход по тем сериям, где субтитров не нашлось.

    Отдельно от обычного прохода и с более редким расписанием: там мы ищем
    новое, здесь — ждём, когда появится то, чего пока нет. Каждый день
    перебирать одно и то же дороже, чем раз в сутки.

    Ограничение «только новые файлы» здесь не применяется: серия уже попала в
    список, то есть её однажды признали своей, и терять её из-за возраста
    файла было бы странно.
    """
    series_ids = pending_misses()
    if not series_ids:
        return {"series": 0, "planned": 0, "queued": 0}

    planned = queued = touched = 0
    for series_id in series_ids:
        try:
            result = await run_for_series(series_id, user_id, automatic=False)
        except Exception:
            log.exception("Повтор автоскачивания: тайтл %s не удался", series_id)
            continue
        if result["planned"]:
            touched += 1
        planned += result["planned"]
        queued += result["queued"]

    log.info(
        "Повтор автоскачивания: тайтлов %d, выбрано %d, поставлено %d",
        touched,
        planned,
        queued,
    )
    return {"series": touched, "planned": planned, "queued": queued}
