# --- Этап 1: сборка фронтенда ---
FROM node:22-alpine AS frontend

WORKDIR /build

# Сначала манифесты — слой с зависимостями переиспользуется, пока они не менялись.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install

COPY frontend/ ./
# Vite кладёт результат в ../backend/app/static, поэтому каталог должен существовать.
RUN mkdir -p /backend/app/static && \
    sed -i "s#'../backend/app/static'#'/backend/app/static'#" vite.config.ts && \
    npm run build


# --- Этап 2: рантайм ---
FROM python:3.12-slim AS runtime

# PYTHONUNBUFFERED — иначе логи застревают в буфере и docker logs пустует.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# tini — корректная обработка сигналов, иначе SIGTERM не доходит до uvicorn
# и контейнер убивается по таймауту, обрывая загрузки.
RUN apt-get update && \
    apt-get install -y --no-install-recommends tini curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir \
        "fastapi==0.115.6" \
        "uvicorn[standard]==0.34.0" \
        "sqlalchemy==2.0.36" \
        "httpx[http2]==0.28.1" \
        "apscheduler==3.11.0" \
        "pyyaml==6.0.2" \
        "rapidfuzz==3.11.0" \
        "watchdog==6.0.0" \
        "argon2-cffi==23.1.0" \
        "python-multipart==0.0.20"

COPY backend/app ./app
# Правила разбора имён кладём внутрь образа. Том ./config — необязательный
# override: без этой копии контейнер, запущенный без bind-mount, падал при
# сканировании библиотеки.
COPY config/filename_rules.yaml ./app/defaults/filename_rules.yaml
COPY --from=frontend /backend/app/static ./app/static

# Непривилегированный пользователь. UID/GID перекрываются через PUID/PGID в compose.
RUN groupadd -g 1000 app && \
    useradd -u 1000 -g 1000 -m -s /bin/bash app && \
    mkdir -p /data /config && \
    chown -R app:app /app /data /config

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
