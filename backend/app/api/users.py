"""Учётные записи и права.

Вход по логину и паролю; у каждой записи свой набор прав. Корневая учётная
запись задаётся окружением (`A365_ADMIN_LOGIN` + `A365_UI_PASSWORD`), заводится
при старте и всегда остаётся администратором — снятая галочка не должна
запирать снаружи.

Почему проверка именно здесь, а не в интерфейсе: спрятанная кнопка не защита.
Тот же запрос отправляется из консоли браузера одной строкой, поэтому право
проверяется зависимостью эндпоинта, а скрытие кнопок — только удобство поверх.

Управление пользователями — только администратор, и отдельным правом это не
выдаётся: право раздавать права и есть администратор.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core import errors
from app.core.db import session_scope
from app.core.errors import ApiError
from app.core.permissions import (
    ALL_PERMISSIONS,
    ASSIGNABLE_PERMISSIONS,
    DEFAULT_PERMISSIONS,
    IMPLICIT_PERMISSIONS,
    CurrentUser,
    Permission,
    current_user,
    require_admin,
)
from app.api.auth import issue_session_after_password_change
from app.core.security import (
    generate_temporary_password,
    hash_password,
    password_problem,
    sessions,
)
from app.models import AppUser

log = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    is_admin: bool = False
    #: Игнорируется у администратора: ему права не урезают.
    permissions: list[str] = Field(default_factory=lambda: list(DEFAULT_PERMISSIONS))
    #: Без пароля под этим именем нельзя войти — запись останется владельцем
    #: задач и привязок. Это законный случай, поэтому поле необязательное.
    password: str | None = None
    #: Пароль выдаётся на один раз: сервер придумывает его сам и показывает
    #: администратору однократно, а владелец задаёт свой при первом входе.
    #:
    #: Взаимоисключающе с `password`: либо администратор задаёт пароль
    #: насовсем, либо мы генерируем временный. Придуманный руками «временный»
    #: пароль на практике оказывается словарным, а живёт он до первого входа —
    #: смысла в такой комбинации нет.
    temporary_password: bool = False


class UserUpdate(BaseModel):
    is_admin: bool | None = None
    permissions: list[str] | None = None
    password: str | None = None
    #: Назначенный пароль по умолчанию временный. `False` — редкий случай
    #: «задать насовсем», когда пароль придумал сам владелец.
    temporary_password: bool | None = None


def _to_dict(row: AppUser) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "is_admin": row.is_admin,
        # У администратора показываем полный набор: так в интерфейсе видно, что
        # он может всё, а не «права не заданы».
        "permissions": ALL_PERMISSIONS if row.is_admin else list(row.permissions or []),
        "has_password": bool(row.password_hash),
        # Администратору видно, что человек ещё не сменил выданный пароль.
        "must_change_password": bool(row.must_change_password),
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
    # Неявные права в запись не пишем: они действуют у всех и подставляются при
    # проверке. Сохранив их здесь, мы получили бы две правды — у одних записей
    # право в списке есть, у других нет, а поведение одинаковое.
    # Порядок канонический, а не порядок нажатий: иначе список в интерфейсе
    # перетасовывался бы при каждом сохранении.
    return [p for p in ASSIGNABLE_PERMISSIONS if p in permissions]


def _checked(password: str) -> str:
    """Пароль, годный к сохранению. Иначе — понятный отказ.

    Проверка стоит на сервере, а не только в форме: правка запроса из консоли
    браузера обходит любую проверку на клиенте.
    """
    problem = password_problem(password)
    if problem:
        raise ApiError(400, errors.SETTINGS_INVALID, problem, reason=problem)
    return password


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
        # Неявные права добавляем в ответ: интерфейс решает по нему, что
        # показывать, и без них он спрятал бы каталог у каждого, кто вошёл.
        "permissions": (
            ALL_PERMISSIONS
            if user.is_admin
            else sorted(set(user.permissions) | IMPLICIT_PERMISSIONS)
        ),
        # Пока режим не включён, интерфейс не должен прятать ничего. Флаг
        # отделяет «прав нет» от «разграничение ещё не действует».
        "multiuser_enabled": _multiuser_enabled(),
        # Пароль временный — интерфейс обязан увести на форму смены. Сервер
        # при этом уже отказывает всему остальному, так что это подсказка,
        # а не защита.
        "must_change_password": user.must_change_password,
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
            # Только назначаемые: каталог есть у всех, кто вошёл, и галочка,
            # которую нельзя снять, в форме только сбивала бы с толку.
            "all_permissions": ASSIGNABLE_PERMISSIONS,
            # Чем предзаполнить галочки в форме создания. Держать этот список
            # на клиенте значило бы иметь вторую правду, которая разойдётся с
            # серверной при первом же изменении.
            "default_permissions": list(DEFAULT_PERMISSIONS),
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

        # Временный пароль придумывает сервер. Открытое значение уходит в
        # ответ и больше нигде не хранится — второй раз узнать его нельзя.
        issued = generate_temporary_password() if payload.temporary_password else None
        plain = issued or payload.password

        row = AppUser(
            name=name,
            is_admin=payload.is_admin,
            permissions=permissions,
            password_hash=hash_password(_checked(plain)) if plain else None,
            # Без пароля флаг бессмыслен: входить под такой записью нечем,
            # а менять нечего.
            must_change_password=bool(issued),
        )
        session.add(row)
        session.flush()
        created = _to_dict(row)

    if issued:
        created["password"] = issued
    return created


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
            row.password_hash = hash_password(_checked(payload.password))
            # Пароль, назначенный администратором, знает не он один — значит
            # владелец обязан заменить его при первом же входе. Снять это
            # можно только сменив пароль самому, и это не лишний шаг: иначе
            # у чужого пароля не будет срока годности вовсе.
            row.must_change_password = payload.temporary_password is not False
            # Чужой пароль меняют по одной причине: он скомпрометирован или
            # человек потерял доступ. В обоих случаях оставлять живыми сессии,
            # открытые старым паролём, — значит не сделать ровно того, ради чего
            # пароль и меняли.
            sessions.drop_for_user(user_id)

        session.flush()
        return _to_dict(row)


@router.post("/{user_id}/password/reset", dependencies=[Depends(require_admin())])
def reset_password(user_id: int) -> dict[str, Any]:
    """Сброс пароля: сервер придумывает новый и показывает его один раз.

    Нужен на случай «человек забыл пароль». Придумывать пароль руками
    администратору не даём: он придумает удобный, а удобный здесь означает
    слабый — этот пароль всё равно живёт до первого входа.

    ⚠️ Открытое значение возвращается **единственный раз**, в ответе на это
    действие. Нигде не хранится и не логируется: в базе только хеш, и второй
    раз узнать пароль нельзя — можно лишь сбросить снова.

    Новый пароль сразу помечен временным, поэтому им можно только войти и
    тут же задать свой. Сессии закрываются: если доступ уводили, старая кука
    не должна пережить сброс.
    """
    plain = generate_temporary_password()
    with session_scope() as session:
        row = session.get(AppUser, user_id)
        if row is None:
            raise ApiError(404, errors.USER_NOT_FOUND, "Пользователь не найден")
        row.password_hash = hash_password(plain)
        row.must_change_password = True
        name = row.name

    sessions.drop_for_user(user_id)
    log.info("Пароль пользователя %s сброшен администратором", user_id)
    return {"id": user_id, "name": name, "password": plain}


class PasswordChange(BaseModel):
    current: str
    new: str


@router.post("/me/password")
def change_own_password(
    payload: PasswordChange,
    request: Request,
    response: Response,
    user: CurrentUser = Depends(current_user),
) -> dict[str, bool]:
    """Смена собственного пароля.

    Доступна любому вошедшему и без всяких прав: свой пароль — единственное, чем
    человек должен распоряжаться сам. Пока этого не было, сменить пароль мог
    только администратор и только чужой — то есть обычный пользователь навсегда
    оставался с тем паролём, который ему однажды продиктовали.

    Старый пароль спрашиваем обязательно: без него оставленная без присмотра
    вкладка превращается в захват учётной записи.
    """
    from app.core.security import login_limiter, verify_user

    # Тот же счётчик, что у формы входа: иначе здесь появился бы способ
    # перебирать пароль без ограничений, просто из-под живой сессии.
    key = f"pwchange:{request.client.host if request.client else 'unknown'}"
    if login_limiter.check(key) > 0:
        raise ApiError(429, errors.RATE_LIMITED, "Слишком много попыток", limit=login_limiter.limit, window=int(login_limiter.window))

    if verify_user(user.name, payload.current) != user.id:
        login_limiter.register_failure(key)
        raise ApiError(403, errors.PERMISSION_DENIED, "Текущий пароль неверен", permission="password")

    problem = password_problem(payload.new)
    if problem:
        raise ApiError(400, errors.SETTINGS_INVALID, problem, reason=problem)

    with session_scope() as session:
        row = session.get(AppUser, user.id)
        if row is None:
            raise ApiError(404, errors.USER_NOT_FOUND, "Пользователь не найден")
        row.password_hash = hash_password(payload.new)
        # Пароль теперь знает только владелец — временным он быть перестал.
        row.must_change_password = False

    login_limiter.reset(key)
    # Остальные сессии закрываем: смена пароля обязана выкидывать того, кто
    # вошёл со старым. Себе тут же выдаём новую, иначе человек сменил пароль и
    # немедленно получил 401 — выглядит как поломка.
    sessions.drop_for_user(user.id)
    issue_session_after_password_change(response, request, user.id)
    return {"ok": True}


@router.delete("/{user_id}", dependencies=[Depends(require_admin())])
def delete_user(user_id: int) -> dict[str, int]:
    with session_scope() as session:
        row = session.get(AppUser, user_id)
        if row is None:
            raise ApiError(404, errors.USER_NOT_FOUND, "Пользователь не найден")
        _guard_last_admin(session, row, becoming_admin=False)
        session.delete(row)
    # Удалённый не должен доходить, куда ходил: без этого его кука жила бы до
    # своего срока. Отказ он получил бы и так — но по факту пропажи записи, а
    # это другое и ломается при первом же совпадении идентификаторов.
    sessions.drop_for_user(user_id)
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
