"""Права доступа.

Заготовка под многопользовательский режим, которого ПОКА НЕТ. Сегодня в базе
один пользователь (`id=1`), он администратор, и ничего не ограничено — поведение
ровно то же, что было до появления этого модуля. Смысл в том, чтобы разграничение
уже существовало в коде и в схеме: включить его потом будет вопросом заведения
второго пользователя, а не переписывания всех эндпоинтов.

Главное правило, ради которого модуль вообще нужен: **проверка на сервере, а не
в интерфейсе**. Спрятать кнопку легко, но кнопка — не защита: тот же запрос
отправляется из консоли браузера одной строкой. Поэтому право проверяется в
зависимости эндпоинта, и скрытие кнопок в UI — только удобство поверх неё.

Права намеренно крупные. Дробить до отдельных действий («менять привязки, но не
запускать скан») смысла нет: это домашний сервис на несколько человек, и разница
между ролями здесь проходит по крупным границам — кто может тратить диск и
трафик, кто может менять общие настройки, а кто просто смотрит.
"""

from __future__ import annotations

import logging
from enum import Enum

from fastapi import Depends, Request

from app.core import errors
from app.core.db import session_scope
from app.core.errors import ApiError
from app.models import DEFAULT_USER_ID, AppUser

log = logging.getLogger(__name__)


class Permission(str, Enum):
    """Категории действий, которые можно разрешить или запретить по отдельности."""

    #: Менять серверные настройки — токен, пути, расписания. Самое опасное:
    #: через настройки видно премиум-токен и задаются пути записи на диск.
    SETTINGS = "settings"

    #: Ставить загрузки в очередь и управлять ею. Тратит диск и трафик.
    DOWNLOADS = "downloads"

    #: Править привязки папок и серий, запускать сканирование библиотеки.
    LIBRARY = "library"

    #: Смотреть онлайн через наш прокси. Отдельно от загрузок намеренно: это
    #: и есть роль «пусть смотрит, но ничего не качает и не меняет».
    WATCH = "watch"


#: Что получает обычный пользователь, если при создании ничего не выбрали.
#: Просмотр — самое безобидное: ни диска, ни общих настроек он не трогает.
DEFAULT_PERMISSIONS = [Permission.WATCH.value]

#: Полный набор — для формы создания пользователя в интерфейсе.
ALL_PERMISSIONS = [p.value for p in Permission]


class CurrentUser:
    """Кто выполняет запрос. Отдельный тип, чтобы не таскать модель из сессии БД."""

    def __init__(self, user_id: int, name: str, is_admin: bool, permissions: list[str]) -> None:
        self.id = user_id
        self.name = name
        self.is_admin = is_admin
        self.permissions = permissions

    def can(self, permission: Permission) -> bool:
        # У администратора прав не спрашивают: иначе достаточно забыть выдать
        # ему одну галочку, чтобы он потерял доступ к собственным настройкам.
        return self.is_admin or permission.value in self.permissions


#: Кем считать запрос, если пользователя определить не удалось.
#:
#: Так бывает штатно: пароль на вход не задан, и middleware сессию не заводит
#: вовсе. Раз пароля нет, разграничивать некого — доступ и так открыт всем, у
#: кого есть сеть до порта, и урезать права было бы имитацией защиты.
_FALLBACK = CurrentUser(DEFAULT_USER_ID, "default", is_admin=True, permissions=ALL_PERMISSIONS)


def current_user(request: Request) -> CurrentUser:
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        return _FALLBACK

    with session_scope() as session:
        row = session.get(AppUser, user_id)
        if row is None:
            # Пользователя удалили, пока его сессия жива. Отказываем: тихо
            # выдать ему права по умолчанию значило бы восстановить доступ,
            # который только что отобрали.
            raise ApiError(
                403,
                errors.PERMISSION_DENIED,
                "Пользователь больше не существует — войдите заново",
            )
        return CurrentUser(row.id, row.name, row.is_admin, list(row.permissions or []))


def require(permission: Permission):
    """Зависимость эндпоинта: пускает дальше только с этим правом."""

    def guard(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not user.can(permission):
            raise ApiError(
                403,
                errors.PERMISSION_DENIED,
                f"Недостаточно прав: требуется «{permission.value}»",
                permission=permission.value,
            )
        return user

    return guard


def require_admin():
    """Только администратор. Для управления самими пользователями."""

    def guard(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not user.is_admin:
            raise ApiError(
                403,
                errors.ADMIN_REQUIRED,
                "Действие доступно только администратору",
            )
        return user

    return guard
