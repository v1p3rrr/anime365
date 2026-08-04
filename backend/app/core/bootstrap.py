"""Инициализация БД при старте: таблицы, FTS, пользователь по умолчанию, слой настроек."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.core.config import settings
from app.core.db import Base, engine, session_scope
from app.core.migrations import add_missing_columns
from app.core.settings_registry import PATH_KEYS, check_path_layout
from app.models import DEFAULT_USER_ID, AppSetting, AppUser
from app.services.fts import init_fts

log = logging.getLogger(__name__)


def bootstrap() -> None:
    # Схема простая и меняется редко, поэтому обходимся create_all вместо Alembic.
    # Если появятся несовместимые изменения — заводим Alembic, но не раньше.
    Base.metadata.create_all(engine)
    # Колонки, добавленные в модели после создания таблиц, дописываем на месте:
    # каталог это кэш, но ронять сервис из-за новой колонки нельзя.
    added = add_missing_columns()
    if added:
        log.info("Схема дополнена: добавлено колонок %d", added)
    init_fts()

    with session_scope() as session:
        if session.get(AppUser, DEFAULT_USER_ID) is None:
            session.add(
                AppUser(id=DEFAULT_USER_ID, name="default", is_admin=True, permissions=[])
            )
            log.info("Создан пользователь по умолчанию")

        # Заполняем колонки роли у записей, заведённых до их появления.
        #
        # add_missing_columns() дописывает колонку, но существующие строки
        # остаются с NULL: значение по умолчанию у модели применяется при
        # вставке, а не задним числом. Без этого прохода единственный
        # пользователь на уже работающей установке перестал бы быть
        # администратором и запер бы сам себя — настройки и управление
        # пользователями закрылись бы, а снять ограничение было бы некому.
        legacy = session.execute(
            select(AppUser).where(AppUser.is_admin.is_(None) | AppUser.permissions.is_(None))
        ).scalars().all()
        for row in legacy:
            if row.is_admin is None:
                row.is_admin = True
            if row.permissions is None:
                row.permissions = []
        if legacy:
            log.info("Права проставлены существующим пользователям: %d", len(legacy))

    load_settings_from_db()

    # После загрузки настроек: имя и пароль администратора берутся из них.
    ensure_admin_user()

    # Проверяем ПОСЛЕ загрузки слоя БД: действующее значение может прийти и из
    # переменной окружения, и из правки в UI. Проверка на эндпоинте настроек
    # ловит только второе — заданные в docker-compose пересекающиеся пути
    # проходили бы мимо неё и разъезжались молча.
    #
    # Падаем, а не предупреждаем. Последствия чинятся только руками: сканер
    # заведёт недокачанные файлы как серии, привязки укажут на мусор, а звук
    # попадёт в видеобиблиотеку. Отказ на старте дешевле разбора этого потом.
    problems = check_path_layout({key: settings.get_str(key) for key in PATH_KEYS})
    if problems:
        raise RuntimeError(
            "Пути в настройках пересекаются, запуск невозможен:\n  " + "\n  ".join(problems)
        )

    if not settings.get_str("ui_password"):
        log.warning(
            "Пароль на UI не задан: вход открыт всем, у кого есть доступ к порту. "
            "Через UI доступен премиум-токен и запись в файловую систему."
        )


def ensure_admin_user() -> None:
    """Приводит главного администратора в соответствие настройкам.

    Зовётся ПОСЛЕ загрузки слоёв настроек: имя и пароль берутся из них, а на
    момент `bootstrap()` слой БД ещё не прочитан.

    Что делает:

    * заводит пользователя с именем из `admin_login`, если такого нет;
    * всегда держит его администратором — иначе достаточно снять галочку, чтобы
      запереть себя снаружи;
    * записывает ему пароль из `ui_password`. Тот же пароль продолжает работать
      и без логина: вход по одному общему паролю никуда не делся, логин просто
      добавляет к нему имя.

    Пароль пересчитывается на каждый старт: он приходит из настроек, а те могли
    смениться в `.env` между запусками, и старый хеш тогда врал бы.
    """
    from app.core.security import hash_password

    login = (settings.get_str("admin_login") or "admin").strip()
    password = settings.get_str("ui_password")
    if not login:
        return

    with session_scope() as session:
        user = session.execute(select(AppUser).where(AppUser.name == login)).scalar_one_or_none()
        if user is None:
            # Первым делом пробуем переименовать пользователя по умолчанию, а не
            # плодить второго: задачи и права уже привязаны к нему, и новый
            # администратор рядом со старым «default» только запутал бы.
            user = session.get(AppUser, DEFAULT_USER_ID)
            if user is not None and user.name == "default":
                log.info("Пользователь по умолчанию стал администратором")
                user.name = login
            else:
                user = AppUser(name=login, is_admin=True, permissions=[])
                session.add(user)
                log.info("Заведена учётная запись администратора")

        user.is_admin = True
        if password:
            # Пароль в настройке может быть уже хешем (его туда мог положить UI)
            # — второй раз хешировать нельзя, иначе войти станет невозможно.
            user.password_hash = password if password.startswith("$argon2") else hash_password(
                password
            )
        else:
            # Пароля нет — значит его нет и у учётной записи. Оставить прежний
            # хеш значило бы, что сброшенный пароль продолжает открывать вход:
            # настройка пуста, а войти по старому по-прежнему можно.
            user.password_hash = None
        # Имя администратора в лог не пишем: логи читают и хранят, а это
        # половина учётных данных. Достаточно сказать, готов вход или нет.
        log.info("Учётная запись администратора настроена, пароль задан: %s", "да" if password else "нет")


def load_settings_from_db() -> None:
    """Поднимает слой UI-оверрайдов поверх дефолтов и переменных окружения."""
    with session_scope() as session:
        rows = session.execute(select(AppSetting.key, AppSetting.value)).all()
    settings.load_db_overrides({key: value for key, value in rows})
    log.info("Загружено переопределений настроек из БД: %d", len(rows))
