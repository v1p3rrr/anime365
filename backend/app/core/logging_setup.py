"""Логирование с вырезанием секретов.

`access_token` бессрочный, поэтому попадание его в лог или в текст трейсбека равносильно
утечке. При первой же ошибке CDN токен окажется в тексте URL, поэтому фильтр обязателен,
а не «на всякий случай».
"""

from __future__ import annotations

import logging
import sys

from app.core.config import settings

_MASK = "***REDACTED***"


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


def setup_logging(level: str = "INFO") -> None:
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

    # httpx логирует полные URL, а в них уходит access_token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
