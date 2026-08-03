"""Эндпоинты настроек.

Форма в интерфейсе строится из реестра, а не пишется руками: иначе она разойдётся
с реальным набором настроек при первом же добавлении параметра.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

# Псевдоним: имя `errors` в этом модуле занято списком ошибок валидации.
from app.core import errors as api_errors
from app.core.errors import ApiError
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.api.auth import issue_session_after_password_change
from app.core import settings_registry as reg
from app.core.bootstrap import load_settings_from_db
from app.core.config import settings
from app.core.security import HASH_PREFIX, hash_password
from app.core.settings_registry import ValidationError
from app.core.permissions import Permission, require
from app.models.catalog import ScanRun
from app.models.settings import AppSetting
from app.services.anime365 import Anime365Client, Anime365Error

log = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

GROUP_LABELS = {
    "api": "Anime365",
    "paths": "Пути",
    "downloads": "Загрузки",
    "schedule": "Расписания",
    "matching": "Сопоставление",
    "files": "Права на файлы",
    "security": "Безопасность",
    "features": "Дополнительно",
}

# Порядок групп в интерфейсе — от того, что настраивают в первую очередь.
GROUP_ORDER = tuple(GROUP_LABELS)


# Какая настройка какой историей джоб иллюстрируется.
HINT_SOURCES = {
    "catalog_full_refresh_hours": "catalog_full",
    "catalog_refresh_hours": "catalog_incr",
    "library_scan_hours": "library",
}


def _live_hints() -> dict[str, str]:
    """Подсказки по фактическим замерам, а не по числам в тексте справки.

    Раньше в справке стояли конкретные значения («44 запроса, ~32 секунды,
    29 тысяч тайтлов»). Они верны для одного дня и одной машины: каталог растёт,
    сеть у всех разная. Показываем последний реальный прогон — он и точнее,
    и заодно отвечает на вопрос «а когда это вообще запускалось».
    """
    from app.core.db import session_scope

    out: dict[str, str] = {}
    with session_scope() as session:
        for key, kind in HINT_SOURCES.items():
            run = session.execute(
                select(ScanRun)
                .where(ScanRun.kind == kind, ScanRun.status == "ok")
                .order_by(ScanRun.finished_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            if run is None or run.finished_at is None:
                out[key] = "Ещё ни разу не выполнялось."
                continue

            seconds = (run.finished_at - run.started_at).total_seconds()
            stats = run.stats or {}
            # У разных джоб разные ключи, поэтому берём первое осмысленное число.
            amount = next(
                (v for k, v in stats.items() if isinstance(v, int) and k != "skipped_junk"),
                None,
            )
            parts = [f"последний прогон {run.finished_at:%d.%m %H:%M}", f"занял {seconds:.0f} с"]
            if amount is not None:
                parts.append(f"обработано {amount:,}".replace(",", " "))
            out[key] = ", ".join(parts).capitalize() + "."
    return out


@router.get("")
def list_settings() -> dict[str, Any]:
    """Все настройки с текущими значениями и источником каждого."""
    items = settings.describe_all()
    hints = _live_hints()
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        item["hint"] = hints.get(item["key"])
        groups.setdefault(item["group"], []).append(item)

    ordered = sorted(groups.items(), key=lambda kv: GROUP_ORDER.index(kv[0]))
    return {
        "groups": [
            {"key": key, "label": GROUP_LABELS.get(key, key), "settings": values}
            for key, values in ordered
        ]
    }


class SettingUpdate(BaseModel):
    key: str
    value: str


def _store(key: str, raw: str) -> None:
    from app.core.db import session_scope

    with session_scope() as session:
        row = session.get(AppSetting, key)
        if row is None:
            session.add(AppSetting(key=key, value=raw))
        else:
            row.value = raw


@router.put(
    "",
    dependencies=[Depends(require(Permission.SETTINGS))],
)
def update_settings(
    updates: Annotated[list[SettingUpdate], Body(embed=True)],
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Сохраняет переопределения из интерфейса.

    Значения проходят ту же валидацию, что и переменные окружения: правка на
    стороне браузера не может протащить недопустимое значение.
    """
    applied: list[str] = []
    errors: list[str] = []
    password_changed = False

    # Пересечение корней проверяем ДО применения и целиком, а не по одному
    # значению: беда возникает от пары путей, и увидеть её, глядя на одно поле,
    # нельзя. Складываем предлагаемые значения поверх действующих — правят
    # обычно один путь, а конфликтует он с уже сохранённым.
    proposed = {key: settings.get_str(key) for key in reg.PATH_KEYS}
    for update in updates:
        if update.key in proposed:
            proposed[update.key] = update.value
    layout_problems = reg.check_path_layout(proposed)
    if layout_problems:
        raise ApiError(
            400,
            api_errors.PATHS_OVERLAP,
            "; ".join(layout_problems),
            reason="; ".join(layout_problems),
        )

    for update in updates:
        try:
            setting = reg.get(update.key)
        except KeyError:
            errors.append(f"{update.key}: неизвестная настройка")
            continue

        if setting.server_only:
            # Ровно тот случай, ради которого заведён флаг: allowlist зеркал
            # нельзя расширить из браузера, иначе весь смысл allowlist теряется.
            errors.append(f"{setting.label_ru}: меняется только на сервере, через переменную окружения")
            continue

        raw = update.value

        if setting.key == "ui_password":
            if raw == "":
                # Пустая строка приходит из формы при каждом сохранении: поле
                # секретное и не показывает текущее значение. Считаем это
                # «не трогать», а не «снять пароль» — снятие делается сбросом.
                continue
            raw = hash_password(raw)
            password_changed = True
        elif setting.secret and raw == "":
            continue

        try:
            setting.validate(setting.parse(raw))
        except ValidationError as exc:
            errors.append(str(exc))
            continue

        _store(setting.key, raw)
        applied.append(setting.key)

    if errors and not applied:
        raise ApiError(400, api_errors.SETTINGS_INVALID, "; ".join(errors), reason="; ".join(errors))

    # Перечитываем слой БД целиком, чтобы значения применились сразу.
    load_settings_from_db()

    if any(key.startswith("jellyfin") or key == "enable_jellyfin" for key in applied):
        # Список элементов и идентификатор сервера кэшируются на пять минут.
        # После правки адреса или ключа держать прежний кэш нельзя: пользователь
        # только что чинил интеграцию и ждёт результата сейчас, а не через
        # пять минут.
        from app.services.jellyfin import jellyfin

        jellyfin.forget()

    if "max_concurrent_downloads" in applied:
        # Число воркеров читается при старте очереди, поэтому без перезапуска
        # правка не делала бы ничего до перезапуска контейнера — а человек
        # видел бы сохранённое значение и считал, что оно работает.
        from app.services.download_queue import download_queue

        download_queue.request_restart()

    if password_changed:
        issue_session_after_password_change(response, request)

    return {"applied": applied, "errors": errors}


@router.post(
    "/reset/{key}",
    dependencies=[Depends(require(Permission.SETTINGS))],
)
def reset_setting(key: str, request: Request, response: Response) -> dict[str, Any]:
    """Убирает переопределение — значение возвращается к переменной окружения."""
    from app.core.db import session_scope

    try:
        setting = reg.get(key)
    except KeyError as exc:
        raise ApiError(404, api_errors.SETTING_UNKNOWN, f"Неизвестная настройка {key}", key=key) from exc

    with session_scope() as session:
        session.execute(delete(AppSetting).where(AppSetting.key == key))

    load_settings_from_db()

    if key.startswith("jellyfin") or key == "enable_jellyfin":
        from app.services.jellyfin import jellyfin

        jellyfin.forget()

    if setting.key == "ui_password":
        issue_session_after_password_change(response, request)

    resolved = settings.resolve(key)
    return {"key": key, "source": resolved.source}


class TokenCheckRequest(BaseModel):
    # Значение прямо из формы, ещё не сохранённое. Иначе пришлось бы сохранять
    # непроверенный токен, чтобы узнать, рабочий ли он.
    token: str | None = None


@router.post(
    "/test-token",
    dependencies=[Depends(require(Permission.SETTINGS))],
)
async def test_token(payload: TokenCheckRequest | None = None) -> dict[str, Any]:
    """Проверка токена и подписки — единственный способ узнать это заранее.

    Без активной подписки скачивание вернёт HTML-страницу вместо файла, поэтому
    лучше выяснить проблему здесь, а не на первой загрузке.
    """
    candidate = (payload.token if payload else None) or settings.get_str("access_token")
    checking_unsaved = bool(payload and payload.token)

    if not candidate:
        return {"ok": False, "reason": "Токен не задан", "checked_unsaved": False}

    try:
        async with Anime365Client() as client:
            me = await client.me(token=candidate)
    except Anime365Error as exc:
        return {
            "ok": False,
            "reason": f"Anime365 недоступен: {exc}",
            "checked_unsaved": checking_unsaved,
        }

    logged_in = bool(me.get("isLogined"))
    premium = bool(me.get("isPremium"))

    if not logged_in:
        reason = (
            "Anime365 не опознал токен. Проверьте, что скопировано значение целиком "
            "и без пробелов по краям."
        )
    elif not premium:
        reason = "Токен рабочий, но премиум-подписка неактивна — скачивание будет недоступно."
    else:
        reason = None

    return {
        "ok": logged_in and premium,
        "logged_in": logged_in,
        "premium": premium,
        "name": me.get("name"),
        "premium_until": me.get("premiumUntil"),
        "checked_unsaved": checking_unsaved,
        "reason": reason,
    }


@router.get("/env-example", response_class=Response)
def env_example() -> Response:
    """`.env.example`, сгенерированный из реестра.

    Отдаётся как файл, чтобы содержимое не расходилось с кодом: править его
    руками нельзя, а копировать из репозитория — лишний шаг при обновлении.
    """
    return Response(
        reg.render_env_example(),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename=".env.example"'},
    )


def password_is_hashed() -> bool:
    """Диагностика: пароль из ENV лежит в открытом виде, из UI — хешем."""
    return settings.get_str("ui_password").startswith(HASH_PREFIX)


@router.post(
    "/test-jellyfin",
    dependencies=[Depends(require(Permission.SETTINGS))],
)
async def test_jellyfin() -> dict[str, Any]:
    """Проверка адреса и ключа Jellyfin.

    Без неё узнать, что ключ неверен, можно было бы только из логов — а туда
    после каждой загрузки никто не смотрит.
    """
    from app.services.jellyfin import jellyfin

    if not settings.get_bool("enable_jellyfin"):
        return {"ok": False, "reason": "Интеграция с Jellyfin выключена"}

    result = await jellyfin.refresh()
    if result.get("ok"):
        result["message"] = "Jellyfin принял запрос на обновление библиотеки"
    return result
