"""Склейка трёх слоёв настроек: default (код) < ENV < БД (правка из UI).

Читать значения только через `settings.get(key)` — так гарантируется единый порядок
приоритетов и валидация. Прямое обращение к os.environ в остальном коде запрещено.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

from app.core import settings_registry as reg
from app.core.settings_registry import Setting, SettingType, ValidationError

log = logging.getLogger(__name__)

MASK = "••••"


@dataclass(frozen=True)
class ResolvedValue:
    """Значение вместе с информацией о том, откуда оно взялось (нужно для UI)."""

    key: str
    value: Any
    source: str  # "default" | "env" | "ui"


class Settings:
    """Кэш эффективных значений. Обновляется при правке настроек из UI."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._env: dict[str, Any] = {}
        self._db: dict[str, Any] = {}
        self._load_env()

    # ------------------------------------------------------------------ #
    # Загрузка слоёв
    # ------------------------------------------------------------------ #

    def _load_env(self) -> None:
        """Читает переменные окружения один раз при старте."""
        empty: list[str] = []
        for s in reg.SETTINGS:
            raw = os.environ.get(s.env)
            if raw is None:
                continue
            if raw == "":
                # Пустая строка — это «не задано», а не значение. Но запомним:
                # десяток пустых переменных подряд означает, что их кто-то
                # выставил и обнулил, и об этом стоит сказать вслух.
                empty.append(s.env)
                continue
            try:
                self._env[s.key] = s.validate(s.parse(raw))
            except ValidationError as exc:
                # Не падаем целиком: игнорируем битую переменную и громко пишем в лог,
                # иначе одна опечатка в compose делает сервис незапускаемым.
                log.error("Игнорирую %s: %s", s.env, exc)

        self._report_env(empty)

    def _report_env(self, empty: list[str]) -> None:
        """Пишет в лог, что именно приехало из окружения.

        Появилось после дня отладки вслепую: настройки из `.env` не доходили до
        приложения, а снаружи это выглядело как «часть параметров не
        подтягивается, а часть будто работает» — на деле работали значения по
        умолчанию, случайно совпавшие с ожидаемыми.

        Причина была в docker compose: `environment:` имеет приоритет над
        `env_file:`, а подстановка `${VAR:-}` не видит файл из `env_file:` и
        подставляет пустую строку. Одна строчка в логе показала бы это сразу.
        """
        if self._env:
            log.info(
                "Из окружения принято настроек: %d (%s)",
                len(self._env),
                ", ".join(sorted(reg.get(key).env for key in self._env)),
            )
        else:
            log.info("Из окружения не принято ни одной настройки — работают значения по умолчанию")

        # Отдельным предупреждением: пустые переменные почти всегда означают
        # не «намеренно очищено», а сломанную подстановку в compose.
        if len(empty) >= 3:
            log.warning(
                "Пустыми пришли %d переменных (%s…). Обычно это значит, что "
                "значения лежат в файле из env_file:, а подстановка ${VAR} в "
                "environment: его не видит и затирает их пустой строкой. "
                "Держите значения в .env рядом с docker-compose.yml",
                len(empty),
                ", ".join(sorted(empty)[:3]),
            )

    def load_db_overrides(self, rows: dict[str, str]) -> None:
        """Принимает сырые строки из таблицы app_setting и заменяет слой UI целиком."""
        parsed: dict[str, Any] = {}
        for key, raw in rows.items():
            try:
                s = reg.get(key)
            except KeyError:
                log.warning("В БД лежит неизвестная настройка %r — игнорирую", key)
                continue
            try:
                parsed[key] = s.validate(s.parse(raw))
            except ValidationError as exc:
                log.error("Игнорирую значение из БД для %s: %s", key, exc)
        with self._lock:
            self._db = parsed

    def set_db_override(self, key: str, raw: str | None) -> None:
        """Добавляет или снимает (raw=None) переопределение из UI."""
        s = reg.get(key)
        with self._lock:
            if raw is None:
                self._db.pop(key, None)
            else:
                self._db[key] = s.validate(s.parse(raw))

    # ------------------------------------------------------------------ #
    # Чтение
    # ------------------------------------------------------------------ #

    def resolve(self, key: str) -> ResolvedValue:
        s = reg.get(key)
        with self._lock:
            if key in self._db:
                return ResolvedValue(key, self._db[key], "ui")
            if key in self._env:
                return ResolvedValue(key, self._env[key], "env")
        return ResolvedValue(key, s.default, "default")

    def get(self, key: str) -> Any:
        return self.resolve(key).value

    def get_int(self, key: str) -> int:
        return int(self.get(key))

    def get_bool(self, key: str) -> bool:
        return bool(self.get(key))

    def get_str(self, key: str) -> str:
        return str(self.get(key))

    def get_list(self, key: str) -> tuple[str, ...]:
        return tuple(self.get(key))

    # ------------------------------------------------------------------ #
    # Для API настроек
    # ------------------------------------------------------------------ #

    def describe_all(self) -> list[dict[str, Any]]:
        """Описание всех настроек для формы в UI. Секреты — только маской."""
        out: list[dict[str, Any]] = []
        for s in reg.SETTINGS:
            resolved = self.resolve(s.key)
            item: dict[str, Any] = {
                "key": s.key,
                "type": s.type.value,
                "group": s.group.value,
                "label": s.label_ru,
                "help": s.help_ru,
                "env": s.env,
                "source": resolved.source,
                "secret": s.secret,
                "server_only": s.server_only,
                "choices": list(s.choices) if s.choices else None,
            }
            if s.secret:
                item["value"] = MASK if resolved.value else ""
                item["is_set"] = bool(resolved.value)
            else:
                item["value"] = (
                    list(resolved.value) if s.type is SettingType.STR_LIST else resolved.value
                )
                item["default"] = (
                    list(s.default) if s.type is SettingType.STR_LIST else s.default
                )
            out.append(item)
        return out

    # ------------------------------------------------------------------ #
    # Производные значения
    # ------------------------------------------------------------------ #

    def allowed_origins(self) -> tuple[str, ...]:
        """Серверный allowlist зеркал. base_url из UI обязан быть в этом списке."""
        return self.get_list("mirrors")

    def base_url(self) -> str:
        """base_url с проверкой по allowlist.

        Подмена значения на стороне браузера бессмысленна: если в БД оказался хост,
        которого нет в allowlist, возвращаем первое зеркало из списка.
        """
        value = self.get_str("base_url").rstrip("/")
        allowed = self.allowed_origins()
        if value not in allowed:
            log.warning("base_url %r отсутствует в allowlist — использую %s", value, allowed[0])
            return allowed[0]
        return value

    def secret_values(self) -> tuple[str, ...]:
        """Все непустые секреты — для вырезания из логов и текстов ошибок."""
        out = []
        for s in reg.SETTINGS:
            if not s.secret:
                continue
            v = self.get(s.key)
            if v:
                out.append(str(v))
        return tuple(out)


settings = Settings()


def describe_setting(key: str) -> Setting:
    return reg.get(key)
