"""Служебные модели: пользователи и оверрайды настроек из UI."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Пока пользователь один и общий. Аккаунтов нет, но таблица и внешние ключи
# заводятся сразу, чтобы включение аккаунтов не требовало миграции данных.
DEFAULT_USER_ID = 1


class AppUser(Base):
    """Учётная запись.

    Многопользовательский режим ПОКА НЕ ВКЛЮЧЁН: в базе одна строка `id=1`, она
    администратор, и вход по-прежнему идёт по одному общему паролю из настроек.
    Колонки роли заведены заранее, чтобы включение сводилось к заведению второй
    строки, а не к переделке схемы и всех эндпоинтов.
    """

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)

    # У администратора прав не спрашивают — см. CurrentUser.can().
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)

    # Список категорий из Permission. Смысл имеет только у не-администратора.
    permissions: Mapped[list] = mapped_column(JSON, default=list)

    # Пароль этого пользователя, argon2-хешем. Пусто — входить под этим именем
    # нельзя: такой пользователь существует только как владелец задач и прав.
    #
    # Хеш, а не пароль: по нему нельзя войти, даже получив копию базы, а
    # перебор argon2 стоит дорого. Наружу не отдаётся никогда — ни в списке
    # пользователей, ни в настройках.
    password_hash: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    """ТОЛЬКО переопределения, сделанные из UI.

    Значения по умолчанию и переменные окружения здесь не хранятся: иначе было бы
    невозможно отличить «пользователь выставил это же значение» от «значение унаследовано»,
    и кнопка сброса перестала бы работать.
    """

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
