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
from app.models.library import (
    EpisodeMapping,
    LibraryFile,
    LibraryFolder,
    LibraryRoot,
    TitleMapping,
)
from app.models.settings import DEFAULT_USER_ID, AppSetting, AppUser

__all__ = [
    "DEFAULT_USER_ID",
    "AppSetting",
    "AppUser",
    "Episode",
    "EpisodeMapping",
    "Genre",
    "LibraryFile",
    "LibraryFolder",
    "LibraryRoot",
    "ScanRun",
    "Series",
    "SeriesGenre",
    "TitleMapping",
    "Translation",
    "TranslationEmbed",
]
