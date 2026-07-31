"""Общие фикстуры тестов."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# БД для тестов — отдельным файлом. Путь читается при импорте приложения,
# поэтому выставляем его раньше любого импорта из app.
_tmp_db = Path(tempfile.gettempdir()) / "a365-tests.sqlite3"
os.environ.setdefault("A365_DB_PATH", str(_tmp_db))

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password, sessions  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Создаёт таблицы один раз на прогон.

    Без этого часть тестов зависела от порядка: таблицы поднимала только
    фикстура `client` (через lifespan приложения), а тесты, которые ходят в
    `session_scope()` напрямую, молча рассчитывали, что кто-то уже отработал
    раньше. На машине разработчика это не проявлялось — файл БД лежит во
    временной папке ОС и переживает прогоны, так что таблицы оставались с
    прошлого раза. На чистой машине (свежий контейнер, CI) двадцать тестов
    падали с «no such table: download_task».
    """
    from app.core.bootstrap import bootstrap

    bootstrap()


@pytest.fixture
def client():
    """Приложение целиком, с поднятыми таблицами и фоновыми задачами."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    settings.set_db_override("ui_password", None)
    sessions.drop_all()


@pytest.fixture
def with_password():
    """Включает пароль на время теста, не трогая БД."""
    settings.set_db_override("ui_password", hash_password("правильный-пароль"))
    yield
    settings.set_db_override("ui_password", None)
    sessions.drop_all()
