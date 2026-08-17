"""Логирование с вырезанием секретов.

`access_token` бессрочный, поэтому попадание его в лог или в текст трейсбека равносильно
утечке. При первой же ошибке CDN токен окажется в тексте URL, поэтому фильтр обязателен,
а не «на всякий случай».
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import yaml

from app.core.config import settings

_MASK = "***REDACTED***"

#: Где искать `logging.yaml` — тот же порядок, что у `filename_rules.yaml`.
#:
#: Первым идёт смонтированный `/config`: правка там переживает пересборку образа
#: и не требует доступа к исходникам. Последним — файл репозитория, чтобы всё
#: работало и при запуске из исходников.
CONFIG_PATH = Path("/config/logging.yaml")
REPO_PATH = Path(__file__).resolve().parents[3] / "config" / "logging.yaml"


class SecretRedactingFilter(logging.Filter):
    """Вырезает значения секретных настроек из сообщений и аргументов."""

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = settings.secret_values()
        if not secrets:
            return True

        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg, secrets)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._redact(str(v), secrets) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    self._redact(a, secrets) if isinstance(a, str) else a for a in record.args
                )

        # Трейсбек кэшируется в exc_text — его тоже чистим.
        if record.exc_text:
            record.exc_text = self._redact(record.exc_text, secrets)

        return True

    @staticmethod
    def _redact(text: str, secrets: tuple[str, ...]) -> str:
        for secret in secrets:
            # Короткие значения не маскируем: слишком велик риск испортить
            # осмысленный текст случайным совпадением.
            if len(secret) >= 8 and secret in text:
                text = text.replace(secret, _MASK)
        return text


def load_levels() -> tuple[str | None, dict[str, str]]:
    """Уровни из `logging.yaml`: общий и точечные. Файла нет — значит пусто.

    Формат намеренно узкий:

    ```yaml
    root: INFO
    loggers:
      app.access: WARNING
    ```

    ⚠️ Не `dictConfig`. Полный конфиг позволил бы заменить обработчик, а к
    нашему прицеплен фильтр, вырезающий `access_token`; токен бессрочен, и одна
    невнимательная строка в конфиге превратила бы лог в утечку. Уровни такого
    риска не несут.

    Испорченный файл не должен мешать приложению запуститься: без логов
    разбираться в поломке нечем, а значит логирование обязано подниматься даже
    тогда, когда его собственный конфиг сломан.
    """
    for candidate in (CONFIG_PATH, REPO_PATH):
        try:
            if not candidate.is_file():
                continue
            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            # Логгер ещё может быть не настроен — пишем в stderr напрямую.
            print(f"Не удалось прочитать {candidate}: {exc}", file=sys.stderr)
            continue

        if not isinstance(data, dict):
            continue

        root = data.get("root")
        root = str(root).strip().upper() if root else None
        if root not in logging.getLevelNamesMapping():
            root = None

        loggers: dict[str, str] = {}
        for name, value in (data.get("loggers") or {}).items():
            level = str(value).strip().upper()
            if name and level in logging.getLevelNamesMapping():
                loggers[str(name)] = level
        return root, loggers

    return None, {}


def parse_levels(raw: str) -> dict[str, str]:
    """`app.access=DEBUG,app.services.matcher=WARNING` → словарь.

    Мусор молча пропускаем: строка приходит из переменной окружения, и ошибка в
    ней не повод не запуститься. Хуже неверного уровня только приложение,
    которое из-за него не поднялось.
    """
    levels: dict[str, str] = {}
    for chunk in raw.split(","):
        name, _, value = chunk.partition("=")
        name, value = name.strip(), value.strip().upper()
        if name and value in logging.getLevelNamesMapping():
            levels[name] = value
    return levels


def setup_logging(level: str | None = None) -> None:
    """Настраивает вывод. Уровень — из аргумента, иначе из окружения.

    Источников три, каждый следующий перекрывает предыдущий:

    1. умолчания в коде — `INFO`;
    2. `config/logging.yaml` — основное место, живёт рядом с библиотекой правил
       разбора имён и правится на сервере без пересборки;
    3. переменные окружения `A365_LOG_LEVEL` и `A365_LOG_LEVELS` — быстрое
       перекрытие «на один запуск», не трогая файл. Формат второй:
       `A365_LOG_LEVELS=app.services.matcher=DEBUG,app.access=WARNING`.

    Имена модулей — те же, что видны в логе третьим полем. Полезные адреса:
    `app.access` (строки доступа со временем ответа), `app.services.matcher`,
    `app.services.library_scan`, `app.services.download_queue`, `httpx`.
    """
    file_root, file_levels = load_levels()

    if level is None:
        level = (os.environ.get("A365_LOG_LEVEL") or file_root or "INFO").strip().upper()
    if level not in logging.getLevelNamesMapping():
        level = "INFO"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.addFilter(SecretRedactingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn заводит свои хендлеры — снимаем их, чтобы всё шло через наш фильтр.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    # Своя строка доступа (`AccessLogMiddleware`) несёт то же самое плюс время
    # ответа, а две строки на запрос — только шум. Уровень, а не `disabled`:
    # предупреждения самого uvicorn о разрыве соединения остаются видны.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # httpx логирует полные URL, а в них уходит access_token.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Точечные уровни идут последними — они должны перебивать всё выше, включая
    # и заглушённый `uvicorn.access`, и `httpx`: если человек просит DEBUG у
    # конкретного модуля, он знает, чего просит.
    overrides = {**file_levels, **parse_levels(os.environ.get("A365_LOG_LEVELS") or "")}
    for name, value in overrides.items():
        logging.getLogger(name).setLevel(value)

    if overrides:
        # Пишем в лог, что именно перекрыто: иначе «почему модуль молчит» станет
        # отдельным расследованием, а причина будет в переменной окружения.
        logging.getLogger(__name__).info(
            "Уровень логирования %s, точечно: %s",
            level,
            ", ".join(f"{name}={value}" for name, value in sorted(overrides.items())),
        )
