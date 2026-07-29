"""Импорт всех моделей, чтобы они зарегистрировались в метаданных Base."""

from app.models.catalog import (
    Episode,
    Genre,
    ScanRun,
    Series,
    SeriesGenre,
    Translation,
    TranslationEmbed,
)
from app.models.settings import DEFAULT_USER_ID, AppSetting, AppUser

__all__ = [
    "DEFAULT_USER_ID",
    "AppSetting",
    "AppUser",
    "Episode",
    "Genre",
    "ScanRun",
    "Series",
    "SeriesGenre",
    "Translation",
    "TranslationEmbed",
]
