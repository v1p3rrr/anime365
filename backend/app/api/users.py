"""Учётные записи и права.

Многопользовательский режим ПОКА НЕ ВКЛЮЧЁН: вход идёт по одному общему паролю,
и все, кто вошёл, работают от администратора `id=1`. Эти эндпоинты нужны, чтобы
записи можно было завести заранее — и чтобы разграничение уже проверялось на
сервере, а не появилось потом задним числом.

Почему проверка именно здесь, а не в интерфейсе: спрятанная кнопка не защита.
Тот же запрос отправляется из консоли браузера одной строкой, поэтому право
проверяется зависимостью эндпоинта, а скрытие кнопок — только удобство поверх.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core import errors
from app.core.db import session_scope
from app.core.errors import ApiError
from app.core.permissions import (
    ALL_PERMISSIONS,
    DEFAULT_PERMISSIONS,
    CurrentUser,
    Permission,
    current_user,
    require_admin,
)
from app.core.security import hash_password
from app.models import AppUser

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    is_admin: bool = False
    #: Игнорируется у администратора: ему права не урезают.
    permissions: list[str] = Field(default_factory=lambda: list(DEFAULT_PERMISSIONS))
    #: Необязателен, пока вход общий. Заранее принимаем, чтобы не заводить
    #: пароль вторым заходом, когда многопользовательский режим включат.
    password: str | None = None


class UserUpdate(BaseModel):
    is_admin: bool | None = None
    permissions: list[str] | None = None
    password: str | None = None


def _to_dict(row: AppUser) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "is_admin": row.is_admin,
        # У администратора показываем полный набор: так в интерфейсе видно, что
        # он может всё, а не «права не заданы».
        "permissions": ALL_PERMISSIONS if row.is_admin else list(row.permissions or []),
        "has_password": bool(row.password_hash),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _validate(permissions: list[str]) -> list[str]:
    unknown = [p for p in permissions if p not in ALL_PERMISSIONS]
    if unknown:
        raise ApiError(
            400,
            errors.PERMISSION_DENIED,
            f"Неизвестное право: {', '.join(unknown)}",
            permission=", ".join(unknown),
        )
    # Порядок канонический, а не порядок нажатий: иначе список в интерфейсе
    # перетасовывался бы при каждом сохранении.
    return [p for p in ALL_PERMISSIONS if p in permissions]


@router.get("/me")
def me(user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    """Права текущего пользователя — по ним интерфейс решает, что показывать.

    Публичнее остальных: нужен на самой первой отрисовке, до того как что-либо
    нарисовано, и отказ здесь означал бы пустую страницу без объяснения.
    """
    return {
        "id": user.id,
        "name": user.name,
        "is_admin": user.is_admin,
        "permissions": ALL_PERMISSIONS if user.is_admin else user.permissions,
        # Пока режим не включён, интерфейс не должен прятать ничего. Флаг
        # отделяет «прав нет» от «разграничение ещё не действует».
        "multiuser_enabled": _multiuser_enabled(),
    }


def _multiuser_enabled() -> bool:
    """Есть ли хоть один не-администратор.

    Пока такого нет, разграничение фактически ни на что не влияет, и интерфейс
    показывает всё как раньше. Это и есть «работаем как админ, ничего не скрываем».
    """
    with session_scope() as session:
        return bool(
            session.execute(
                select(func.count(AppUser.id)).where(AppUser.is_admin.is_(False))
            ).scalar()
        )


@router.get("", dependencies=[Depends(require_admin())])
def list_users() -> dict[str, Any]:
    with session_scope() as session:
        rows = session.execute(select(AppUser).order_by(AppUser.id)).scalars().all()
        return {
            "users": [_to_dict(r) for r in rows],
            "all_permissions": ALL_PERMISSIONS,
            "multiuser_enabled": any(not r.is_admin for r in rows),
        }


@router.post("", dependencies=[Depends(require_admin())])
def create_user(payload: UserCreate) -> dict[str, Any]:
    permissions = [] if payload.is_admin else _validate(payload.permissions)

    name = payload.name.strip()

    with session_scope() as session:
        # Сравнение регистронезависимое, но в Python, а не в SQL: встроенная
        # `lower()` у SQLite работает только с ASCII, и «Дубль» с «дубль» она
        # считает разными именами. Строк здесь единицы, так что цена нулевая.
        taken = name.casefold()
        exists = any(
            (row.name or "").casefold() == taken
            for row in session.execute(select(AppUser)).scalars()
        )
        if exists:
            raise ApiError(
                409, errors.USER_EXISTS, f"Пользователь «{name}» уже есть", name=name
            )

        row = AppUser(
            name=name,
            is_admin=payload.is_admin,
            permissions=permissions,
            password_hash=hash_password(payload.password) if payload.password else None,
        )
        session.add(row)
        session.flush()
        return _to_dict(row)


@router.put("/{user_id}", dependencies=[Depends(require_admin())])
def update_user(user_id: int, payload: UserUpdate) -> dict[str, Any]:
    with session_scope() as session:
        row = session.get(AppUser, user_id)
        if row is None:
            raise ApiError(404, errors.USER_NOT_FOUND, "Пользователь не найден")

        if payload.is_admin is not None:
            _guard_last_admin(session, row, becoming_admin=payload.is_admin)
            row.is_admin = payload.is_admin
        if payload.permissions is not None:
            row.permissions = _validate(payload.permissions)
        if payload.password:
            row.password_hash = hash_password(payload.password)

        session.flush()
        return _to_dict(row)


@router.delete("/{user_id}", dependencies=[Depends(require_admin())])
def delete_user(user_id: int) -> dict[str, int]:
    with session_scope() as session:
        row = session.get(AppUser, user_id)
        if row is None:
            raise ApiError(404, errors.USER_NOT_FOUND, "Пользователь не найден")
        _guard_last_admin(session, row, becoming_admin=False)
        session.delete(row)
    return {"deleted": user_id}


def _guard_last_admin(session, row: AppUser, becoming_admin: bool) -> None:
    """Не даёт остаться без единого администратора.

    Иначе настройки и управление пользователями закрылись бы навсегда: снять
    ограничение было бы некому, и оставался бы только доступ к файлу базы.
    """
    if not row.is_admin or becoming_admin:
        return

    others = session.execute(
        select(func.count(AppUser.id)).where(AppUser.is_admin.is_(True), AppUser.id != row.id)
    ).scalar()
    if not others:
        raise ApiError(
            409,
            errors.CANNOT_REMOVE_LAST_ADMIN,
            "Это последний администратор — иначе управлять настройками станет некому",
        )
