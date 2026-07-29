"""Инициализация БД при старте: таблицы, FTS, пользователь по умолчанию, слой настроек."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.core.config import settings
from app.core.db import Base, engine, session_scope
from app.models import DEFAULT_USER_ID, AppSetting, AppUser
from app.services.fts import init_fts

log = logging.getLogger(__name__)


def bootstrap() -> None:
    # Схема простая и меняется редко, поэтому обходимся create_all вместо Alembic.
    # Если появятся несовместимые изменения — заводим Alembic, но не раньше.
    Base.metadata.create_all(engine)
    init_fts()

    with session_scope() as session:
        if session.get(AppUser, DEFAULT_USER_ID) is None:
            session.add(AppUser(id=DEFAULT_USER_ID, name="default"))
            log.info("Создан пользователь по умолчанию")

    load_settings_from_db()

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
