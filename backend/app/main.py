"""Точка входа FastAPI.

Фронтенд собирается в статику и раздаётся отсюда же — отдельного контейнера с Nginx нет.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import catalog, downloads, health, library, maintenance, title
from app.core.bootstrap import bootstrap
from app.core.db import check_connection
from app.core.logging_setup import setup_logging
from app.services.download_queue import download_queue
from app.workers.scheduler import start_scheduler, stop_scheduler

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    log.info("Anime365 Downloader запускается")

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
    # Документацию API оставляем — она сильно упрощает отладку руками.
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.include_router(health.router, prefix="/api")
app.include_router(catalog.router, prefix="/api")
app.include_router(title.router, prefix="/api")
app.include_router(library.router, prefix="/api")
app.include_router(downloads.router, prefix="/api")
app.include_router(maintenance.router, prefix="/api")


if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        """Отдаёт index.html на все не-API маршруты — это SPA с клиентским роутингом."""
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
