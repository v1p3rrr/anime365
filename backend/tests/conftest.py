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


#: Учётные данные, под которыми входят тесты. Логин обязателен: входа «по
#: одному паролю без имени» больше нет — без имени не работает ни разделение
#: прав, ни счётчик попыток по учётной записи.
TEST_LOGIN = "тест-админ"
TEST_PASSWORD = "правильный-пароль"


@pytest.fixture
def with_password():
    """Включает вход на время теста и заводит учётную запись администратора."""
    from app.core.bootstrap import ensure_admin_user

    settings.set_db_override("admin_login", TEST_LOGIN)
    settings.set_db_override("ui_password", hash_password(TEST_PASSWORD))
    ensure_admin_user()
    yield {"login": TEST_LOGIN, "password": TEST_PASSWORD}
    settings.set_db_override("admin_login", None)
    settings.set_db_override("ui_password", None)
    sessions.drop_all()


@pytest.fixture(autouse=True)
def scheduler_paused(monkeypatch):
    """Планировщик в тестах заводится, но на паузе: джобы есть, запусков нет.

    ⚠️ Фикстура `client` поднимает приложение целиком, а с ним и планировщик.
    Инкремент каталога стоит со сдвигом ноль — то есть срабатывает сразу
    при старте и идёт на живой сайт. За полный прогон в общую тестовую базу
    набегало около шести тысяч настоящих тайтлов, среди них «Mekaku City
    Actors» (3225), чьи названия совпадают с тестовой папкой «Kagerou Days», и
    `test_mapping_matrix` падал через раз — смотря по тому, успевала ли
    синхронизация до него. Заодно это был поход в сеть из тестов.

    Пауза, а не пустой `start_scheduler`: расписание собирается как в проде, и
    тест может его осмотреть; не срабатывает только сам запуск джоб.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from app.workers import scheduler as jobs

    class PausedScheduler(AsyncIOScheduler):
        def start(self, paused: bool = False) -> None:
            super().start(paused=True)

    monkeypatch.setattr(jobs, "AsyncIOScheduler", PausedScheduler)


#: Походы в настоящую сеть за прогон — для отчёта в конце (см. `no_real_network`).
_NETWORK_ATTEMPTS: list[str] = []


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch, request):
    """Тесты не ходят в настоящую сеть: сетевой транспорт httpx заглушён.

    ⚠️ Прежде каждый такой поход закрывали точечно — автоскачивание
    (`site_refresh`), планировщик (`scheduler_paused`), — и каждый находили по
    уже случившейся беде: таймауту на чистой машине или шести тысячам живых
    тайтлов в тестовой базе. Здесь закрыт сам транспорт: `MockTransport` и
    транспорт `TestClient` работают как прежде, а запрос наружу получает
    `ConnectError` — тот же отказ, что без сети, — и роняет тест, который его
    сделал. Нужен настоящий сайт — это отдельный интеграционный тест, а не
    модульный.

    Петля (`127.0.0.1:1` и т. п.) тоже получает отказ, но тест не роняет: так
    нарочно изображают «сервер не отвечает», и отказ — ровно то, чего тест
    ждёт. Замер при заведении фикстуры: наружу ходил один тест — права на
    просмотр звали настоящий embed на всех четырёх зеркалах.
    """
    import httpx

    def refuse(transport, req: httpx.Request):
        where = f"{req.method} {req.url.host} ← {request.node.nodeid}"
        if req.url.host not in ("127.0.0.1", "localhost", "::1"):
            _NETWORK_ATTEMPTS.append(where)
        raise httpx.ConnectError(f"тест пошёл в сеть: {where}", request=req)

    async def refuse_async(transport, req: httpx.Request):
        refuse(transport, req)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse_async)
    before = len(_NETWORK_ATTEMPTS)
    yield
    attempts = _NETWORK_ATTEMPTS[before:]
    assert not attempts, "тест пошёл в настоящую сеть:\n" + "\n".join(attempts)


@pytest.fixture(autouse=True)
def site_refresh(monkeypatch):
    """Автоскачивание не ходит на сайт за переводами: поход подменён.

    ⚠️ Без этой заглушки движок при пустом или устаревшем кэше зовёт
    `_refresh_translations`, а тот — настоящий `Anime365Client`. Первый же
    прогон после появления обновления ушёл в `smotret-anime.app` прямо из
    теста и прошёл только потому, что сеть была. На чистой машине это был бы
    таймаут на каждый тест с недостающей серией.

    Фикстура отдаёт `calls` — тайтлы, за которыми движок «сходил» (тесты
    обновления проверяют по нему, что поход случился или не случился), и
    `original` — настоящий поход, для тестов его самого.
    """
    from app.services import autodownload

    class SiteRefresh:
        #: Тайтлы, за которыми движок «сходил».
        calls: list[int]
        #: Настоящий поход — для тестов его самого, с подменёнными
        #: `ensure_details`/`ensure_translations`.
        original = staticmethod(autodownload._refresh_translations)

        def __init__(self) -> None:
            self.calls = []

    recorder = SiteRefresh()

    async def fake_refresh(series_id: int) -> bool:
        recorder.calls.append(series_id)
        return False

    monkeypatch.setattr(autodownload, "_refresh_translations", fake_refresh)
    # Память об отказах — состояние модуля; тест, уронивший поход, не должен
    # выключать походы всем следующим.
    monkeypatch.setattr(autodownload, "_refresh_paused_until", None)
    monkeypatch.setattr(autodownload, "_failed_series", {})
    monkeypatch.setattr(autodownload, "_consecutive_failures", [])
    return recorder
