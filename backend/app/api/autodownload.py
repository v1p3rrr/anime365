"""Настройки автоскачивания субтитров и его запуск.

Всё здесь закрыто правом `downloads`, и это решение пользователя: автоскачивание
ставит задачи в общую очередь, то есть тратит диск и трафик сервера ровно так
же, как нажатая руками кнопка «скачать». Отдельного права ему не заводили —
оно отвечало бы на тот же вопрос «можно ли тратить общий ресурс».

Языки везде раздельны. Одно и то же действие для `ru` и для `en` — это два
разных запроса с разными телами, а не одно с флагами: у каждого языка свой
режим, свой список и свои переключатели.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.core import errors
from app.core.db import session_scope
from app.core.errors import ApiError
from app.core.permissions import CurrentUser, Permission, require
from app.models.autodownload import (
    AUTO_LANGS,
    AutoDownloadMiss,
    GLOBAL_MODES,
    TITLE_MODES,
    AutoDownloadRule,
    SeriesAutoDownload,
)
from app.models.catalog import Series
from app.services import autodownload
from app.services.autodownload_rules import configured_series_ids, resolve_for_series
from app.services.subtitle_authors import normalize_author, suggest_authors

router = APIRouter(prefix="/autodownload", tags=["autodownload"])


def _check_lang(lang: str) -> str:
    if lang not in AUTO_LANGS:
        raise ApiError(
            400, errors.SETTINGS_INVALID, f"Неизвестный язык: {lang}", reason=lang
        )
    return lang


class RuleUpdate(BaseModel):
    """Глобальная настройка одного языка."""

    mode: str = Field("off")
    #: Имена групп в том виде, в каком их ввёл человек. Нормализацию делает
    #: сервер: иначе список, набранный руками, не совпал бы с каталогом.
    priorities: list[str] = Field(default_factory=list)
    #: Группы, от которых не качаем никогда. Сильнее любого разрешения.
    blacklist: list[str] = Field(default_factory=list)
    allow_any_fallback: bool = False
    allow_any: bool = False
    ignore_embedded: bool = False


class SeriesUpdate(BaseModel):
    """Настройка одного языка у конкретного тайтла.

    `None` у переключателей означает «наследовать глобальное». Без третьего
    состояния его было бы не отличить от «выключено».
    """

    mode: str = Field("default")
    priorities: list[str] = Field(default_factory=list)
    #: Пустой — наследовать общий, как и приоритеты.
    blacklist: list[str] = Field(default_factory=list)
    allow_any_fallback: bool | None = None
    allow_any: bool | None = None
    ignore_embedded: bool | None = None


def _clean_priorities(values: list[str]) -> list[str]:
    """Нормализованные имена без повторов, порядок сохраняется.

    Повторы убираем молча: человек мог выбрать группу из подсказки и следом
    вписать её же руками, а два одинаковых пункта в списке приоритетов не
    значат ничего.
    """
    out: list[str] = []
    for value in values:
        key = normalize_author(value)
        if key and key not in out:
            out.append(key)
    return out


@router.get("", dependencies=[Depends(require(Permission.DOWNLOADS))])
def get_rules() -> dict[str, Any]:
    """Глобальные настройки всех языков разом — их всего три."""
    with session_scope() as session:
        rows = {
            row.lang: row for row in session.execute(select(AutoDownloadRule)).scalars()
        }
        return {
            "languages": AUTO_LANGS,
            "modes": GLOBAL_MODES,
            "rules": {
                lang: {
                    "mode": (rows[lang].mode if lang in rows else "off"),
                    "priorities": list(rows[lang].priorities or []) if lang in rows else [],
                    "blacklist": list(rows[lang].blacklist or []) if lang in rows else [],
                    "allow_any_fallback": bool(
                        rows[lang].allow_any_fallback if lang in rows else False
                    ),
                    "allow_any": bool(rows[lang].allow_any if lang in rows else False),
                    "ignore_embedded": bool(
                        rows[lang].ignore_embedded if lang in rows else False
                    ),
                }
                for lang in AUTO_LANGS
            },
        }


@router.put("/{lang}", dependencies=[Depends(require(Permission.DOWNLOADS))])
def set_rule(lang: str, payload: RuleUpdate) -> dict[str, Any]:
    _check_lang(lang)
    if payload.mode not in GLOBAL_MODES:
        raise ApiError(
            400, errors.SETTINGS_INVALID, f"Неизвестный режим: {payload.mode}",
            reason=payload.mode,
        )

    with session_scope() as session:
        row = session.get(AutoDownloadRule, lang)
        was_off = row is None or (row.mode or "off") == "off"
        if row is None:
            row = AutoDownloadRule(lang=lang)
            session.add(row)

        # Отметка «когда включили» ставится только на переходе из выключенного.
        # Правка списка приоритетов у работающего языка не должна сдвигать
        # границу: иначе каждое сохранение переставало бы считать своими файлы,
        # приехавшие с прошлого раза.
        if payload.mode != "off" and was_off:
            row.enabled_at = datetime.utcnow()
        elif payload.mode == "off":
            row.enabled_at = None

        row.mode = payload.mode
        row.priorities = _clean_priorities(payload.priorities)
        row.blacklist = _clean_priorities(payload.blacklist)
        row.allow_any_fallback = payload.allow_any_fallback
        row.allow_any = payload.allow_any
        row.ignore_embedded = payload.ignore_embedded
    return get_rules()


@router.get("/authors", dependencies=[Depends(require(Permission.DOWNLOADS))])
def authors(
    lang: Annotated[str, Query(description="ru | en | ja")],
    query: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    """Подсказка для поля ввода: группы, встреченные среди субтитров.

    Список копится сам, по мере открытия тайтлов, поэтому у свежей установки
    он пуст — и это нормально: имя всегда можно вписать руками.
    """
    _check_lang(lang)
    return {"authors": suggest_authors(lang, query)}


@router.get("/configured", dependencies=[Depends(require(Permission.DOWNLOADS))])
def configured() -> dict[str, Any]:
    """Тайтлы с собственной настройкой — чтобы видеть, где она вообще есть.

    Без такого списка настройка тайтла становится невидимой: задал её полгода
    назад, а вспомнить, у кого именно, нечем.
    """
    ids = configured_series_ids()
    if not ids:
        return {"series": []}

    with session_scope() as session:
        titles = {
            row.id: row
            for row in session.execute(select(Series).where(Series.id.in_(ids))).scalars()
        }
        rows = (
            session.execute(
                select(SeriesAutoDownload).where(SeriesAutoDownload.series_id.in_(ids))
            )
            .scalars()
            .all()
        )

    by_series: dict[int, list[SeriesAutoDownload]] = {}
    for row in rows:
        by_series.setdefault(row.series_id, []).append(row)

    out = []
    for series_id in ids:
        series = titles.get(series_id)
        out.append(
            {
                "series_id": series_id,
                "title_ru": getattr(series, "title_ru", None),
                "title_romaji": (getattr(series, "titles", None) or {}).get("romaji"),
                "poster_url": getattr(series, "poster_url", None),
                "languages": {
                    row.lang: {
                        "mode": row.mode,
                        "has_own_list": bool(row.priorities),
                    }
                    for row in by_series.get(series_id, [])
                },
            }
        )
    return {"series": out}


@router.get("/series/{series_id}", dependencies=[Depends(require(Permission.DOWNLOADS))])
def series_settings(series_id: int) -> dict[str, Any]:
    """Что задано у тайтла и что из этого следует.

    Отдаём и заданное, и действующее: без второго интерфейс не сможет
    объяснить, почему при «по умолчанию» ничего не качается — причина в
    глобальном режиме, а он в другом месте.
    """
    with session_scope() as session:
        rows = {
            row.lang: row
            for row in session.execute(
                select(SeriesAutoDownload).where(SeriesAutoDownload.series_id == series_id)
            ).scalars()
        }
        own = {
            lang: {
                "mode": rows[lang].mode,
                "priorities": list(rows[lang].priorities or []),
                "blacklist": list(rows[lang].blacklist or []),
                "allow_any_fallback": rows[lang].allow_any_fallback,
                "allow_any": rows[lang].allow_any,
                "ignore_embedded": rows[lang].ignore_embedded,
            }
            for lang in AUTO_LANGS
            if lang in rows
        }

    effective = resolve_for_series(series_id)
    return {
        "series_id": series_id,
        "modes": TITLE_MODES,
        "own": own,
        "effective": {
            lang: {
                "priorities": resolved.priorities,
                "blacklist": resolved.blacklist,
                "allow_any_fallback": resolved.allow_any_fallback,
                "allow_any": resolved.allow_any,
                "ignore_embedded": resolved.ignore_embedded,
                "from_series": resolved.from_series,
            }
            for lang, resolved in effective.items()
        },
    }


@router.put(
    "/series/{series_id}/{lang}", dependencies=[Depends(require(Permission.DOWNLOADS))]
)
def set_series(series_id: int, lang: str, payload: SeriesUpdate) -> dict[str, Any]:
    _check_lang(lang)
    if payload.mode not in TITLE_MODES:
        raise ApiError(
            400, errors.SETTINGS_INVALID, f"Неизвестный режим: {payload.mode}",
            reason=payload.mode,
        )

    with session_scope() as session:
        if session.get(Series, series_id) is None:
            raise ApiError(404, errors.SERIES_NOT_FOUND, "Тайтл не найден")
        row = session.execute(
            select(SeriesAutoDownload).where(
                SeriesAutoDownload.series_id == series_id,
                SeriesAutoDownload.lang == lang,
            )
        ).scalar_one_or_none()
        if row is None:
            row = SeriesAutoDownload(series_id=series_id, lang=lang)
            session.add(row)
        row.mode = payload.mode
        row.priorities = _clean_priorities(payload.priorities)
        row.blacklist = _clean_priorities(payload.blacklist)
        row.allow_any_fallback = payload.allow_any_fallback
        row.allow_any = payload.allow_any
        row.ignore_embedded = payload.ignore_embedded

    return series_settings(series_id)


@router.delete(
    "/series/{series_id}/{lang}", dependencies=[Depends(require(Permission.DOWNLOADS))]
)
def clear_series(series_id: int, lang: str) -> dict[str, Any]:
    """Убрать свою настройку — вернуться к общей логике."""
    _check_lang(lang)
    with session_scope() as session:
        session.execute(
            delete(SeriesAutoDownload).where(
                SeriesAutoDownload.series_id == series_id,
                SeriesAutoDownload.lang == lang,
            )
        )
    return series_settings(series_id)


@router.post("/run", dependencies=[Depends(require(Permission.DOWNLOADS))])
async def run_all(
    only_configured: Annotated[
        bool, Query(description="Только тайтлы с собственной настройкой")
    ] = False,
    user: CurrentUser = Depends(require(Permission.DOWNLOADS)),
) -> dict[str, Any]:
    """Поиск по библиотеке. Человек нажал осознанно — ограничений по сроку нет.

    Охват выбирается при нажатии, а не берётся из глобального режима: язык
    может стоять в режиме «всем», но пройтись разово хочется лишь по тем
    тайтлам, где приоритеты заданы руками.
    """
    return await autodownload.run_for_all(user_id=user.id, only_configured=only_configured)


@router.get("/misses", dependencies=[Depends(require(Permission.DOWNLOADS))])
def misses() -> dict[str, Any]:
    """Серии, к которым субтитров не нашлось.

    Список нужен человеку, а не программе: по числу попыток и дате первого
    промаха видно, стоит ли ещё ждать перевода. Лишнее выкидывается руками —
    незачем годами спрашивать сайт о том, чего заведомо не будет.
    """
    with session_scope() as session:
        rows = (
            session.execute(
                select(AutoDownloadMiss)
                .where(AutoDownloadMiss.dismissed.is_(False))
                .order_by(AutoDownloadMiss.first_missed_at.desc())
                .limit(500)
            )
            .scalars()
            .all()
        )
        titles = {
            row.id: row
            for row in session.execute(
                select(Series).where(Series.id.in_({r.series_id for r in rows}))
            ).scalars()
        } if rows else {}

        return {
            "misses": [
                {
                    "id": row.id,
                    "series_id": row.series_id,
                    "title_ru": getattr(titles.get(row.series_id), "title_ru", None),
                    "title_romaji": (
                        getattr(titles.get(row.series_id), "titles", None) or {}
                    ).get("romaji"),
                    "episode": row.episode,
                    "lang": row.lang,
                    "checks": row.checks,
                    "first_missed_at": row.first_missed_at.isoformat()
                    if row.first_missed_at
                    else None,
                    "last_checked_at": row.last_checked_at.isoformat()
                    if row.last_checked_at
                    else None,
                }
                for row in rows
            ]
        }


@router.delete("/misses/{miss_id}", dependencies=[Depends(require(Permission.DOWNLOADS))])
def dismiss_miss(miss_id: int) -> dict[str, Any]:
    """Выкинуть запись: больше не проверяем.

    Помечаем, а не удаляем: удалённую строку следующий же проход завёл бы
    заново, и отписаться было бы нельзя.
    """
    with session_scope() as session:
        row = session.get(AutoDownloadMiss, miss_id)
        if row is None:
            raise ApiError(404, errors.NOT_FOUND, "Запись не найдена")
        row.dismissed = True
    return {"dismissed": miss_id}


@router.post("/misses/retry", dependencies=[Depends(require(Permission.DOWNLOADS))])
async def retry_all(
    user: CurrentUser = Depends(require(Permission.DOWNLOADS)),
) -> dict[str, Any]:
    """Перепроверить весь список сейчас, не дожидаясь расписания."""
    return await autodownload.retry_misses(user_id=user.id)


@router.post(
    "/series/{series_id}/run", dependencies=[Depends(require(Permission.DOWNLOADS))]
)
async def run_series(
    series_id: int, user: CurrentUser = Depends(require(Permission.DOWNLOADS))
) -> dict[str, Any]:
    return await autodownload.run_for_series(series_id, user_id=user.id)
