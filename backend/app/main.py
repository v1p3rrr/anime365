"""Точка входа FastAPI.

Фронтенд собирается в статику и раздаётся отсюда же — отдельного контейнера с Nginx нет.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    auth,
    catalog,
    downloads,
    events,
    health,
    library,
    maintenance,
    settings as settings_api,
    stream,
    title,
    users,
)
from app.core import middleware
from app.core import proxy
from app.core.bootstrap import bootstrap
from app.core.db import check_connection
from app.core.logging_setup import setup_logging
from app.core.permissions import Permission, require
from app.services.download_queue import download_queue
from app.workers.scheduler import start_scheduler, stop_scheduler

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    log.info("Anime365 Downloader запускается")
    proxy.log_status()

    if not check_connection():
        log.error("БД недоступна — сервис поднимется, но работать не сможет")

    bootstrap()
    start_scheduler()
    # Воркеры очереди живут в том же событийном цикле: отдельный процесс не нужен,
    # загрузка это чистый ввод-вывод.
    download_queue.start()

    yield

    await download_queue.stop()
    stop_scheduler()
    log.info("Anime365 Downloader останавливается")


app = FastAPI(
    title="Anime365 Downloader",
    version="0.1.0",
    lifespan=lifespan,
    # Штатные маршруты документации выключены и заведены ниже вручную: встроенным
    # нельзя навесить зависимость, а значит и право. Сессию с них спрашивает
    # middleware, но одной сессии мало — карта всех эндпоинтов вместе со схемой
    # настроек это разведка, и обычному пользователю она ни к чему.
    docs_url=None,
    openapi_url=None,
)

middleware.install(app)


@app.get("/api/openapi.json", include_in_schema=False)
def openapi_schema(_: object = Depends(require(Permission.SETTINGS))) -> JSONResponse:
    return JSONResponse(app.openapi())


@app.get("/api/docs", include_in_schema=False)
def swagger_ui(_: object = Depends(require(Permission.SETTINGS))) -> HTMLResponse:
    """Документацию API оставляем — она сильно упрощает отладку руками.

    ⚠️ Сама страница Swagger UI тянет скрипты и стили с CDN, а наша CSP
    (`script-src 'self'`) их не пропускает — в браузере она останется пустой.
    Рабочий способ — забрать `/api/openapi.json` и открыть его чем угодно.
    Так было и до появления этого маршрута; чинить не стали: класть в образ
    бандл Swagger UI ради отладки дороже, чем один `curl`.
    """
    return get_swagger_ui_html(openapi_url="/api/openapi.json", title="Anime365 Downloader")

app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(catalog.router, prefix="/api")
app.include_router(title.router, prefix="/api")
app.include_router(library.router, prefix="/api")
app.include_router(downloads.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(settings_api.router, prefix="/api")
app.include_router(stream.router, prefix="/api")
app.include_router(maintenance.router, prefix="/api")
app.include_router(users.router, prefix="/api")


if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        """Отдаёт index.html на все не-API маршруты — это SPA с клиентским роутингом."""
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
