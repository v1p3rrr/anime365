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


def load_settings_from_db() -> None:
    """Поднимает слой UI-оверрайдов поверх дефолтов и переменных окружения."""
    with session_scope() as session:
        rows = session.execute(select(AppSetting.key, AppSetting.value)).all()
    settings.load_db_overrides({key: value for key, value in rows})
    log.info("Загружено переопределений настроек из БД: %d", len(rows))
