"""Служебные модели: пользователи и оверрайды настроек из UI."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Пока пользователь один и общий. Аккаунтов нет, но таблица и внешние ключи
# заводятся сразу, чтобы включение аккаунтов не требовало миграции данных.
DEFAULT_USER_ID = 1


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
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
