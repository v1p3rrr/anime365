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
from app.models.downloads import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    DownloadTask,
    TaskStatus,
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
    "ACTIVE_STATUSES",
    "DEFAULT_USER_ID",
    "TERMINAL_STATUSES",
    "AppSetting",
    "AppUser",
    "DownloadTask",
    "Episode",
    "EpisodeMapping",
    "Genre",
    "LibraryFile",
    "LibraryFolder",
    "LibraryRoot",
    "ScanRun",
    "Series",
    "SeriesGenre",
    "TaskStatus",
    "TitleMapping",
    "Translation",
    "TranslationEmbed",
]
