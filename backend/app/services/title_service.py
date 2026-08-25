"""Сборка данных для страницы тайтла."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.db import session_scope
from app.services.subtitle_authors import remember_authors
from app.core.events import bus
from app.models.catalog import Episode, Series, Translation, TranslationEmbed
from app.models.library import LibraryFile
from app.services.anime365 import Anime365Client, Anime365Error
from app.services.audio import normalize_lang
from app.services.coverage import (
    NON_CONTENT_EPISODE_TYPES,
    is_placeholder_episode,
    median_duration,
    resolve_is_movie,
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


async def ensure_details(series_id: int, force: bool = False) -> Series | None:
    """Догружает описания, ссылки и эпизоды — их нет в списочном ответе каталога.

    `force` перечитывает всё заново. ⚠️ Без него список серий застывал навсегда:
    флаг `has_details` ставится один раз, полный синк каталога его намеренно не
    трогает, и новых серий у онгоинга страница больше не видела. Заметно это
    стало, когда заготовки будущих серий перестали показываться: серия выходит,
    её запись на сайте становится активной — а у нас в кэше по-прежнему
    заготовка.
    """
    with session_scope() as session:
        series = session.get(Series, series_id)
        if series is not None and series.has_details and not force:
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
            e.id: e for e in session.execute(
                select(Episode).where(Episode.series_id == series_id)
            ).scalars()
        }
        for item in episodes:
            # ⚠️ Существующие записи именно обновляются, а не пропускаются.
            # Раньше здесь стоял `continue`, и запись, однажды попавшая в кэш,
            # больше не менялась никогда. Для заготовки будущей серии это
            # означало бы, что она останется заготовкой и после выхода серии.
            row = existing.get(item["id"])
            if row is None:
                row = Episode(id=item["id"], series_id=series_id)
                session.add(row)
            row.episode_int = str(item.get("episodeInt") or "")
            row.episode_num = _parse_episode_num(item.get("episodeInt"))
            row.episode_full = item.get("episodeFull") or ""
            row.episode_title = item.get("episodeTitle") or ""
            row.episode_type = item.get("episodeType") or ""
            row.first_uploaded = item.get("firstUploadedDateTime")
            row.is_active = bool(item.get("isActive", 1))

    # Ответ содержит ВЕСЬ список серий тайтла, поэтому по нему видно и то, чего
    # там больше нет. У тайтла 40024 Anime365 удалил ONA-нарезку после выхода
    # седьмой серии — у нас она жила в кэше и продолжала предлагаться к
    # скачиванию, хотя ссылки уже не существовало.
    _reconcile_with_site(
        series_id, present_episodes={item["id"] for item in episodes if item.get("id")}
    )

    with session_scope() as session:
        return session.get(Series, series_id)


#: Тайтлы, переводы которых прямо сейчас догружаются фоном.
#:
#: Открыть один тайтл в двух вкладках — обычное дело, и без этого набора обе
#: пошли бы в Anime365 за одним и тем же списком в две тысячи записей.
_refreshing: set[int] = set()

#: Тайтлы, у которых догрузка упала: что случилось и когда пробовать снова.
_failures: dict[int, "RefreshFailure"] = {}

#: Первый отступ после неудачи и потолок, до которого он растёт.
#:
#: Шестьдесят секунд — чтобы разовый сбой сети не был заметен вовсе: страница
#: откроется из кэша, а следующий заход всё починит. Потолок в полчаса — чтобы
#: недоступность Anime365 на всю ночь не стоила тысяч запросов.
REFRESH_RETRY_BASE = timedelta(seconds=60)
REFRESH_RETRY_CAP = timedelta(minutes=30)


@dataclass(frozen=True)
class RefreshFailure:
    """Последняя неудачная догрузка переводов тайтла."""

    at: datetime
    attempts: int
    message: str

    @property
    def retry_at(self) -> datetime:
        # Показатель ограничен: без этого при долгой недоступности множитель
        # разрастается до чисел, на которых timedelta переполняется.
        step = REFRESH_RETRY_BASE * 2 ** min(self.attempts - 1, 10)
        return self.at + min(step, REFRESH_RETRY_CAP)


def _remember_failure(series_id: int, exc: BaseException) -> None:
    """Запоминает неудачу и решает, насколько отложить следующую попытку."""
    previous = _failures.get(series_id)
    attempts = previous.attempts + 1 if previous is not None else 1
    failure = RefreshFailure(
        at=datetime.utcnow(),
        attempts=attempts,
        message=str(exc) or exc.__class__.__name__,
    )
    _failures[series_id] = failure

    # Полная трассировка — только на первой неудаче. Дальше сбой уже описан, и
    # повторять её значит топить лог: на сервере пользователя одна
    # недоступность Anime365 давала строчку каждые двадцать секунд сутками.
    if attempts == 1:
        log.exception("Фоновое обновление переводов тайтла %s не удалось", series_id)
    else:
        log.warning(
            "Фоновое обновление переводов тайтла %s не удалось (попытка %s), "
            "следующая не раньше %s: %s",
            series_id,
            attempts,
            failure.retry_at.strftime("%H:%M:%S"),
            failure.message,
        )


def translation_refresh_failure(series_id: int) -> RefreshFailure | None:
    """Неудача догрузки, если после неё ещё не было успешной."""
    return _failures.get(series_id)


def forget_refresh_failure(series_id: int | None = None) -> None:
    """Забывает неудачу: следующая попытка пойдёт немедленно.

    Нужно там, где повтор запросил человек — он ждать отступа не обязан.
    Без аргумента забывается всё; этим пользуются тесты.
    """
    if series_id is None:
        _failures.clear()
    else:
        _failures.pop(series_id, None)


def translations_are_fresh(series_id: int) -> bool:
    """Есть ли у тайтла непросроченные переводы в кэше."""
    with session_scope() as session:
        oldest = session.execute(
            select(func.min(Translation.synced_at)).where(Translation.series_id == series_id)
        ).scalar()
    return oldest is not None and oldest > datetime.utcnow() - TRANSLATIONS_TTL


def schedule_translation_refresh(series_id: int) -> bool:
    """Ставит догрузку переводов фоном. True — если задача заведена.

    ⚠️ Раньше страница тайтла **ждала** этот поход в Anime365. Замерено на
    сервере пользователя: 3,3 с у Фрирен (2334 перевода), 4,1 с и **13,6 с** у
    других тайтлов — при том что всё остальное на странице берётся из локальной
    базы за десятки миллисекунд. Через шесть часов (`TRANSLATIONS_TTL`) история
    повторялась, и любой тайтл снова открывался секунды.

    Теперь страница отдаётся сразу с тем, что есть в кэше, а обновление идёт
    фоном; клиент видит флаг `translations_pending` и перезапрашивает.

    ⚠️ **Неудача обязана запоминаться.** Раньше не запоминалась, и это давало
    вечный цикл: клиент опрашивает страницу раз в полторы секунды, каждый опрос
    заводит новую задачу, задача падает за десять секунд, `finally` снимает
    флаг — и следующий опрос заводит следующую. На сервере пользователя это
    крутилось сутками: спиннер «подгружаются с Anime365» не гас никогда, а в лог
    падало предупреждение каждые двадцать секунд. Причём сама сеть была
    исправна — проверено десятью запросами из того же контейнера.

    Теперь после неудачи повтор откладывается (`RefreshFailure.retry_at`), а
    клиент получает текст ошибки и время следующей попытки вместо бесконечного
    ожидания.
    """
    if series_id in _refreshing:
        return False

    failure = _failures.get(series_id)
    if failure is not None and datetime.utcnow() < failure.retry_at:
        return False

    _refreshing.add(series_id)

    async def _run() -> None:
        try:
            # Список серий обновляется тем же фоном и с той же периодичностью:
            # у онгоинга новая серия и новые переводы приезжают вместе, а
            # отдельного расписания ради одного запроса заводить незачем.
            await ensure_details(series_id, force=True)
            await ensure_translations(series_id, force=True)
        except Exception as exc:
            # Фоновая задача не имеет права ронять ничего: страница уже отдана.
            _remember_failure(series_id, exc)
        else:
            # Получилось — прошлые неудачи больше ничего не значат.
            _failures.pop(series_id, None)
        finally:
            _refreshing.discard(series_id)

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        # Вне цикла событий (тесты, CLI) фоновая задача не нужна.
        _refreshing.discard(series_id)
        return False
    return True


def translations_pending(series_id: int) -> bool:
    return series_id in _refreshing


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
        # Каталог групп перевода наполняется здесь и только здесь. Отдельного
        # обхода каталога нет намеренно: 29 тысяч тайтлов ради списка групп не
        # стоят ни времени, ни запросов к чужому API — а так список копится
        # сам, по мере того как пользователь открывает страницы.
        remember_authors(rows_for_db)

        # `translations()` обходит все страницы, поэтому здесь ВЕСЬ список
        # переводов тайтла — по нему видно и то, чего на сайте больше нет.
        # Сверяем только на непустом ответе: см. пояснение в `_reconcile_with_site`.
        _reconcile_with_site(
            series_id, present_translations={row["id"] for row in rows_for_db}
        )

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


def _bound_translation_ids(session, series_id: int) -> set[int]:
    """Переводы тайтла, по которым у нас лежит скачанный файл.

    Связь идёт через `t{id}` в имени файла: сканер вынимает его и кладёт в
    `LibraryFile.anime365_translation_id`. Это единственный точный признак —
    разметка серий знает только НОМЕР, а под одним номером у тайтла бывает
    несколько разных серий ([D-170](../../../claude/09-decisions.md)).
    """
    ours = select(Translation.id).where(Translation.series_id == series_id)
    rows = session.execute(
        select(LibraryFile.anime365_translation_id).where(
            LibraryFile.anime365_translation_id.in_(ours)
        )
    ).scalars()
    return {value for value in rows if value is not None}


def _forget_or_mark(session, rows: list[Any], bound: set[int], own_id) -> tuple[int, int]:
    """Общая развилка для исчезнувшего: удалить или пометить.

    Правило пользователя: чего не жалко — удаляем, к чему привязан файл —
    оставляем с пометкой. Второе не роскошь: `t{id}` из имени файла ведёт
    именно в эти строки, и без них скачанное осталось бы без автора, языка и
    серии — безымянным файлом на диске.
    """
    now = datetime.utcnow()
    deleted = marked = 0
    for row in rows:
        if own_id(row) in bound:
            if row.missing_since is None:
                row.missing_since = now
                marked += 1
        else:
            session.delete(row)
            deleted += 1
    return deleted, marked


def _reconcile_with_site(series_id: int, present_episodes: set[int] | None = None,
                         present_translations: set[int] | None = None) -> dict[str, int]:
    """Сводит наш кэш с тем, что сейчас есть на сайте.

    ⚠️ **Пустой ответ не считается доказательством удаления.** Сеть отвалилась,
    токен протух, сайт отдал пустую страницу — и мы бы стёрли весь кэш тайтла.
    Ровно на этих граблях уже стоял обход библиотеки: там «отсутствует в
    результате» означает «нет на диске», и частичный результат снёс бы всё
    остальное ([D-155](../../../claude/09-decisions.md), бэклог §1.4). Поэтому
    сверка работает только по НЕПУСТОМУ списку.

    Вернувшееся на сайт снимает пометку: удалили по ошибке и вернули — обычное
    дело, а запись, помеченная навсегда, врала бы.
    """
    stats = {"episodes_deleted": 0, "episodes_marked": 0,
             "translations_deleted": 0, "translations_marked": 0, "restored": 0}

    with session_scope() as session:
        bound = _bound_translation_ids(session, series_id)

        if present_translations:
            rows = list(
                session.execute(
                    select(Translation).where(Translation.series_id == series_id)
                ).scalars()
            )
            for row in rows:
                if row.id in present_translations and row.missing_since is not None:
                    row.missing_since = None
                    stats["restored"] += 1
            gone = [row for row in rows if row.id not in present_translations]
            deleted, marked = _forget_or_mark(session, gone, bound, lambda row: row.id)
            stats["translations_deleted"] = deleted
            stats["translations_marked"] = marked

        if present_episodes:
            rows = list(
                session.execute(
                    select(Episode).where(Episode.series_id == series_id)
                ).scalars()
            )
            # У серии своих файлов нет — привязка идёт через её переводы.
            bound_episodes = {
                row.episode_id
                for row in session.execute(
                    select(Translation).where(
                        Translation.series_id == series_id, Translation.id.in_(bound or {0})
                    )
                ).scalars()
                if row.episode_id is not None
            }
            for row in rows:
                if row.id in present_episodes and row.missing_since is not None:
                    row.missing_since = None
                    stats["restored"] += 1
            gone = [row for row in rows if row.id not in present_episodes]
            deleted, marked = _forget_or_mark(
                session, gone, bound_episodes, lambda row: row.id
            )
            stats["episodes_deleted"] = deleted
            stats["episodes_marked"] = marked

    if any(stats.values()):
        log.info(
            "Тайтл %s сведён с сайтом: серий удалено %s, помечено %s; "
            "переводов удалено %s, помечено %s; вернулось %s",
            series_id,
            stats["episodes_deleted"],
            stats["episodes_marked"],
            stats["translations_deleted"],
            stats["translations_marked"],
            stats["restored"],
        )
    return stats


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
        "url": Anime365Client.rewrite_host(row.url or ""),
        # Перевода на сайте больше нет: скачать его не выйдет, ссылка мертва.
        "missing": row.missing_since is not None,
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


def _local_audio_langs(
    item: dict[str, Any], lang_by_translation: dict[int, str | None]
) -> set[str]:
    """Языки звука одного локального видеофайла.

    Три источника по убыванию надёжности:
      1. Файл скачан нами — язык известен из самого перевода, гадать не нужно.
      2. `<streamdetails>` из .nfo — список дорожек, записанный Jellyfin.
      3. Языковой суффикс в имени файла (`… .ru.mp4`) — так подписывают озвучку.

    Пустое множество означает «не знаем». Это не то же самое, что «японский»:
    у файла без .nfo и без суффикса дорожка может быть любой, и UI обязан в
    этом случае показать всё, а не отфильтровать по догадке.
    """
    translation_id = item.get("translation_id")
    if translation_id is not None:
        lang = lang_by_translation.get(translation_id)
        if lang in ("ru", "en", "ja"):
            return {lang}

    streams = item.get("streams") or {}
    from_nfo = {
        normalized
        for raw in streams.get("audio_languages") or []
        if (normalized := normalize_lang(raw))
    }
    if from_nfo:
        return from_nfo

    suffix = normalize_lang(item.get("lang"))
    return {suffix} if suffix else set()


def _episode_url(translations: list[dict[str, Any]]) -> str | None:
    """Адрес страницы серии на сайте — из адреса любого её перевода.

    Ссылка перевода выглядит так:
        https://host/catalog/yuru-yuri-260/2-seriya-57744/ozvuchka-216078
    Последний сегмент — сам перевод; всё до него и есть страница серии.

    Отдельного поля у эпизода в API нет (проверено обходом ответа), а
    конструировать адрес по номеру нельзя: в него входит идентификатор эпизода,
    который из номера не выводится.
    """
    for item in translations:
        # Хвостовой слеш встречается и превратил бы последний сегмент в пустой.
        url = (item.get("url") or "").rstrip("/")
        head, _, tail = url.rpartition("/")
        # Хвост должен быть сегментом перевода, а не пустым (ссылка со слешем
        # на конце) и не самим адресом (ссылки без пути).
        if head and tail and "-seriya-" in head:
            return head
    return None


async def build_title_page(series_id: int, force_refresh: bool = False) -> dict[str, Any] | None:
    series = await ensure_details(series_id)
    if series is None:
        return None

    # ⚠️ Страница не ждёт сеть.
    #
    # Шапка, описание, список серий и локальное наличие лежат в нашей базе и
    # отдаются за миллисекунды. Переводы приходят из Anime365, и их у
    # популярного тайтла бывает больше двух тысяч — именно они превращали
    # открытие в 13 секунд ожидания. Поэтому берём то, что есть в кэше, а
    # просроченное обновляем фоном.
    if force_refresh or not translations_are_fresh(series_id):
        # Обновление запросил человек — ждать отступа после прошлой неудачи он
        # не обязан, иначе кнопка «обновить» выглядела бы сломанной.
        if force_refresh:
            forget_refresh_failure(series_id)
        schedule_translation_refresh(series_id)

    with session_scope() as session:
        translations = [
            _translation_dict(row)
            for row in session.execute(
                select(Translation).where(Translation.series_id == series_id)
            ).scalars()
        ]

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
            # ⚠️ Через зеркало, а не как есть. API всегда отдаёт ссылки на
            # smotret-anime.org, и если он заблокирован или отвечает 404 (а он
            # отвечает — проверено), ссылка на странице тайтла ведёт в никуда.
            # Для ссылок скачивания эта замена была с самого начала, для
            # ссылки на тайтл — нет.
            "url": Anime365Client.rewrite_host(series.url or ""),
            "mal_score": series.mal_score,
            "is_airing": series.is_airing,
            # None — решение не принято, действует автоопределение по типу.
            "treat_as_movie": series.treat_as_movie,
            # Считается ниже, когда известно число содержательных серий.
            "effective_is_movie": None,
        }

    _attach_episode_numbers(translations, episodes)

    # Трейлеры и реклама лежат вперемешку с сериями и имеют собственные номера,
    # поэтому в подсчёт «всего серий» они не входят. Заготовки будущих серий —
    # тоже: см. `is_placeholder_episode`.
    content_episodes = [
        e
        for e in episodes
        if e.episode_type not in NON_CONTENT_EPISODE_TYPES
        and not is_placeholder_episode(e.episode_type, e.is_active)
    ]
    # Связка из нескольких movie-эпизодов — не фильм: у частей должны быть
    # разные имена файлов, а у фильма номера серии в имени нет.
    series_data["effective_is_movie"] = resolve_is_movie(
        series_data["treat_as_movie"], series_data["type"], len(content_episodes)
    )
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

    # Когда привязки нет, папка создастся сама при первой загрузке. Показать её
    # имя надо заранее: иначе о том, куда легли файлы, узнают уже по факту.
    # Импорт локальный — download_queue тянет за собой очередь и воркеры,
    # а на уровне модуля это дало бы цикл.
    from app.services.download_queue import propose_folder

    proposed_folder = propose_folder(series_id) if not mappings else None

    # Что из ролей уже лежит локально по каждой серии — чтобы в списке серий было
    # видно, чего не хватает, и не приходилось качать уже имеющееся.
    have_by_episode: dict[float, list[str]] = {}
    # Язык звука локального видео. Нужен, чтобы не предлагать то, что уже есть:
    # имея оригинальную дорожку, человек смотрит с субтитрами, и озвучки ему
    # не нужны, а имея озвучку — наоборот, оригинал ещё впереди.
    audio_by_episode: dict[float, set[str]] = {}
    # Скачанные видеофайлы по сериям — чтобы можно было посмотреть то, что уже
    # лежит на диске, не обращаясь к сайту. Файлов на серию бывает несколько
    # (озвучка и оригинал), поэтому список, а не одно значение.
    local_video_by_episode: dict[float, list[dict[str, Any]]] = {}
    lang_by_translation = {t["id"]: t.get("type_lang") for t in normalized}

    for item in get_local_files(series_id):
        have_by_episode.setdefault(item["episode"], []).append(item["role"])
        if item["role"] != "video":
            continue
        local_video_by_episode.setdefault(item["episode"], []).append(
            {
                "file_id": item["file_id"],
                "filename": item["filename"],
                "size_bytes": item["size_bytes"],
                "langs": sorted(_local_audio_langs(item, lang_by_translation)),
            }
        )
        langs = audio_by_episode.setdefault(item["episode"], set())
        langs.update(_local_audio_langs(item, lang_by_translation))

    # Длительности по серии: считаются по всем переводам, включая те, что
    # отфильтрованы по языку. Язык на хронометраж не влияет, а выборка шире —
    # значит медиана устойчивее.
    # ⚠️ Ключ — идентификатор серии, а НЕ её номер. Под одним номером у тайтла
    # живёт несколько разных серий: замерено на базе пользователя — 48 таких пар
    # в 42 тайтлах. Чаще всего это трейлер рядом с первой серией (28 случаев), но
    # бывает и альтернативная нарезка: у тайтла 40024 двенадцатиминутные
    # ONA-серии повторяют первые шесть телевизионных.
    #
    # Пока ключом был номер, длительности сливались в одно ведро, и медиана
    # 24-минутной серии показывалась у 12-минутной ONA. В базе при этом лежат
    # верные числа: 1433 с у телевизионной первой серии против 869 с у ONA.
    durations_by_episode: dict[int, list[float]] = {}
    for item in normalized:
        episode_id = item.get("episode_id")
        if episode_id is None or not item.get("duration"):
            continue
        # Трейлеры отсекаем и при ключе по идентификатору: они не попадают в
        # `typical_duration` ниже, а он считается по всем значениям этого
        # словаря. У Фрирен трейлер длиной 50 секунд иначе тянет вниз типичную
        # длительность серии всего тайтла.
        if item.get("episode_type") in NON_CONTENT_EPISODE_TYPES:
            continue
        durations_by_episode.setdefault(episode_id, []).append(item["duration"])

    # Адрес страницы серии на сайте. Считаем по ПОЛНЫМ записям переводов:
    # в by_episode ниже уходит урезанный набор полей для клиента, и `url`
    # в него не входит.
    #
    # ⚠️ Ключ — идентификатор эпизода, а НЕ его номер. Номер для этого не
    # годится: у трейлеров он свой и начинается с единицы, поэтому первая серия
    # и трейлер к ней имеют одинаковый `episode_num`. Кто первым попадался в
    # списке переводов, тот и занимал ключ — и кнопка «открыть серию» на первой
    # серии вела на страницу трейлера. Ровно это и увидел пользователь:
    #   …/treyler-1-seriya-356260/…  вместо  …/1-seriya-370730/
    # Соседний цикл про длительность эту ловушку учитывал, здесь её упустили.
    url_by_episode: dict[int, str] = {}
    for item in normalized:
        episode_id = item.get("episode_id")
        if episode_id is None or episode_id in url_by_episode:
            continue
        page = _episode_url([item])
        if page:
            url_by_episode[episode_id] = page

    # Переводы, сгруппированные по серии: из них строится выбор при загрузке
    # конкретной серии.
    #
    # ⚠️ Ключ — идентификатор серии, а НЕ её номер, по той же причине, что у
    # длительностей и у `url_by_episode` выше. Пока ключом был номер, в список
    # первой серии подмешивались чужие переводы: у тайтла 40024 к пяти
    # телевизионным добавлялись два от ONA-нарезки, и получалось семь вариантов
    # русских субтитров с тремя «Crunchyroll» вместо двух. Трейлеры делали то же
    # самое ещё у двух десятков тайтлов — просто там это не бросалось в глаза.
    by_episode: dict[int, list[dict[str, Any]]] = {}
    for item in normalized:
        episode_id = item.get("episode_id")
        if episode_id is None or item.get("type_lang") not in ("ru", "en", "ja"):
            continue
        # Пропавшее с сайта не предлагаем: ссылки нет, попытка кончится отказом.
        # Требование пользователя — показывать в таком случае только то, что уже
        # лежит файлом.
        if item.get("missing"):
            continue
        by_episode.setdefault(episode_id, []).append(
            {
                "id": item["id"],
                "lang": item.get("type_lang"),
                "kind": item.get("type_kind"),
                "authors": item.get("authors_summary") or "без автора",
                "height": item.get("height"),
            }
        )

    # Заготовки будущих серий на страницу не попадают: обещать серию, которой
    # нет и не будет, хуже, чем не показать ничего. Исключение — заготовка, к
    # которой у пользователя уже лежит файл: скрыть её значило бы спрятать его
    # собственные данные из-за чужого флага.
    shown_episodes = [
        e
        for e in episodes
        if not is_placeholder_episode(e.episode_type, e.is_active)
        or e.episode_num in have_by_episode
    ]

    refresh_failure = translation_refresh_failure(series_id)

    return {
        "series": series_data,
        "mappings": mappings,
        # Заполнено только при отсутствии привязки — иначе показывать нечего.
        "proposed_folder": proposed_folder,
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
                # Серии на сайте больше нет. Показываем — у пользователя к ней
                # может лежать скачанный файл, — но скачивать предлагать нечего.
                "missing": e.missing_since is not None,
                "is_content": e.episode_type not in NON_CONTENT_EPISODE_TYPES,
                # Ссылка на страницу серии на сайте. API её не отдаёт, но схема
                # видна в ссылках самих переводов, которые мы храним:
                #   {url тайтла}/{N}-seriya-{id эпизода}/{перевод}
                # Отрезав хвост перевода, получаем адрес серии. Выведено из
                # данных, а не угадано — но если переводов нет, ссылки не будет:
                # выдумывать её мы не станем.
                # ⚠️ У пропавшей серии ссылку не отдаём. Проверено запросом:
                # сайт на такой адрес отвечает НЕ 404, а цепочкой редиректов
                # 302→302→301 на страницу тайтла. То есть кнопка «открыть
                # серию» сработала бы и увела человека не туда, ничего не
                # объяснив. Заодно это причина, по которой удаление нельзя
                # определять запросом ссылки — только сверкой со списком.
                "url": None if e.missing_since else url_by_episode.get(e.id),
                # ⚠️ Локальное — по НОМЕРУ серии, и это не оплошность. Файл на
                # диске назван `S01E01` и ни о какой ONA-нарезке не знает: для
                # него серия — это номер. Переводы и длительность выше ключуются
                # по идентификатору, потому что там источник другой, сайт.
                #
                # Обратная сторона честная: скачав перевод ONA-серии, вы
                # получите файл в той же ячейке `S01E01`, и «есть локально»
                # загорится у обеих строк. Отличить их всё же можно — в имени
                # наших файлов стоит `t{id}` перевода, и по нему поднимается
                # цепочка «файл → перевод → серия → тип».
                # Что уже есть локально: video, sub_ru, sub_ja, sub_en.
                "have": sorted(set(have_by_episode.get(e.episode_num, []))),
                # Языки звука локального видео. Пустой список при наличии видео
                # означает «не удалось определить» — в этом случае UI не прячет
                # ничего, потому что скрывать по догадке хуже, чем показать лишнее.
                "audio_langs": sorted(audio_by_episode.get(e.episode_num, set())),
                # Что можно посмотреть прямо с диска, без обращения к сайту.
                "local_videos": local_video_by_episode.get(e.episode_num, []),
                # Секунды. None — переводов с известной длительностью нет.
                "duration": median_duration(durations_by_episode.get(e.id, [])),
                # Доступные переводы этой серии — для выбора при загрузке.
                "translations": by_episode.get(e.id, []),
            }
            for e in shown_episodes
        ],
        "total_episodes": total,
        # Типичная длительность серии по тайтлу — медиана серийных медиан.
        # Двойная медиана не даёт одной длинной премьере сдвинуть общую цифру.
        "typical_duration": median_duration(
            [
                d
                for d in (
                    median_duration(values) for values in durations_by_episode.values()
                )
                if d
            ]
        ),
        "summary": build_summary(normalized, total, series_data["type"]),
        # Точные ступени берём из кэша embed: если пользователь уже нажимал
        # «уточнить», сводка обязана показывать факт, а не прежнюю прикидку.
        "quality": build_quality_summary(normalized, total, exact_heights_for_series(series_id)),
        # Переводы догружаются прямо сейчас: клиент перезапросит страницу и
        # получит их. Пустой список при этом флаге означает «ещё не приехали»,
        # а не «их нет» — разница для человека принципиальная.
        "translations_pending": translations_pending(series_id),
        # Неудача догрузки — отдельный факт, и молчать о ней нельзя. Пустая
        # сводка без объяснения читается как «переводов нет», а вечный спиннер —
        # как «сейчас приедут»; оба варианта неправда.
        "translations_error": refresh_failure.message if refresh_failure else None,
        "translations_retry_at": (
            refresh_failure.retry_at.isoformat() if refresh_failure else None
        ),
    }


def cached_has_subtitles(translation_id: int) -> bool | None:
    """Известно ли уже, вшиты ли субтитры. `None` — не проверяли.

    Только кэш, без сети: используется там, где переводов сотни (батч), и
    запрос на каждый превратил бы постановку очереди в многоминутную операцию.
    """
    with session_scope() as session:
        cached = session.get(TranslationEmbed, translation_id)
        return None if cached is None else cached.has_subtitles


async def get_embed(
    translation_id: int, force: bool = False, with_sizes: bool = False
) -> dict[str, Any]:
    """Ленивая загрузка embed с кэшированием признаков.

    Кэшируем только устойчивое: вшиты ли субтитры, какие ступени доступны и
    сколько весит файл на каждой. Сами ссылки короткоживущие и подписанные,
    поэтому воркер загрузки запрашивает embed заново непосредственно перед
    скачиванием.

    `with_sizes` стоит отдельного круга запросов к CDN (HEAD на ступень),
    поэтому включается только когда размеры действительно показывают.
    """
    if not force:
        with session_scope() as session:
            cached = session.get(TranslationEmbed, translation_id)
            fresh = cached is not None and cached.fetched_at > datetime.utcnow() - EMBED_TTL
            # Размеры досчитываем и на свежем кэше: их могли ещё не запрашивать.
            if fresh and (not with_sizes or cached.download_sizes):
                return {
                    "translation_id": translation_id,
                    "has_subtitles": cached.has_subtitles,
                    "heights": cached.download_heights,
                    "stream_heights": cached.stream_heights or [],
                    "sizes": cached.download_sizes or {},
                    "from_cache": True,
                }

    async with Anime365Client() as client:
        info = await client.embed(translation_id)
        sizes = await client.download_sizes(info) if with_sizes else {}

    with session_scope() as session:
        session.merge(
            TranslationEmbed(
                translation_id=translation_id,
                has_subtitles=info.subtitles_url is not None,
                subtitles_url=info.subtitles_url,
                download_heights=info.heights,
                stream_heights=info.stream_heights,
                download_sizes=sizes,
                fetched_at=datetime.utcnow(),
            )
        )

    return {
        "translation_id": translation_id,
        "has_subtitles": info.subtitles_url is not None,
        "heights": info.heights,
        "stream_heights": info.stream_heights,
        "sizes": sizes,
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
