"""Настройки должны доезжать из файла окружения до приложения.

Здесь сторожится ошибка, которая стоила пользователю дня отладки на сервере:
настройки из его env-файла не доходили вообще, а снаружи это выглядело как
«часть параметров работает, часть нет» — на деле работали значения по
умолчанию, случайно совпавшие с ожидаемыми.

Причина была в compose. Подстановка `${VAR}` в `environment:` выполняется при
разборе файла и берёт значения только из окружения оболочки и из `.env`; файла
из `env_file:` для неё не существует. А `environment:` имеет приоритет над
`env_file:` — поэтому строка `- A365_ACCESS_TOKEN=${A365_ACCESS_TOKEN:-}`
подставляла пустую строку и затирала значение из подключённого файла.

Проверено на живом compose, включая голую форму `- A365_ACCESS_TOKEN` без «=»:
она гасит `env_file:` точно так же.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.settings_registry import SETTINGS

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"

# Настройки, которых нет в реестре: они меняются только через окружение, формы
# в интерфейсе у них нет. В шаблоне перечислены наравне с остальными, иначе про
# них никто не узнает.
ENV_ONLY = {
    "A365_TRANSLATION_SCAN_HOURS",
    "A365_TRANSLATION_SCAN_RPM",
    "A365_TRANSLATION_SCAN_SCOPE",
    "A365_BACKFILL_MAX",
}


def _lines_without_comments(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _example_vars() -> set[str]:
    return set(re.findall(r"^(A365_[A-Z0-9_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M))


class TestComposeDoesNotClobberEnvFile:
    """Главная проверка: compose не должен затирать значения из env-файла."""

    def test_env_file_is_declared(self) -> None:
        lines = _lines_without_comments(COMPOSE.read_text(encoding="utf-8"))
        assert any(line.strip().startswith("env_file:") for line in lines), (
            "из compose пропал env_file: — настройки приложения браться неоткуда"
        )

    def test_no_app_settings_in_environment(self) -> None:
        """Ни одной A365_-переменной в `environment:` быть не должно.

        Достаточно одной такой строки, чтобы значение из env-файла молча
        обнулилось — причём именно у той настройки, которую человек и правил.
        """
        lines = _lines_without_comments(COMPOSE.read_text(encoding="utf-8"))
        offenders = [line.strip() for line in lines if re.match(r"^\s*- A365_", line)]
        assert not offenders, (
            f"в environment: снова появились настройки приложения: {offenders}. "
            "Они перекроют env_file: и обнулят его значения"
        )


class TestExampleIsComplete:
    """`.env.example` — единственное место, где список настроек полный.

    Он же шаблон для рабочего файла, поэтому пропуск здесь означает настройку,
    о существовании которой никто не узнает.
    """

    def test_every_setting_is_in_example(self) -> None:
        missing = {s.env for s in SETTINGS} - _example_vars()
        assert not missing, (
            f"в .env.example нет переменных: {sorted(missing)}. "
            "Настройку добавили в реестр, но в шаблоне её нет"
        )

    def test_env_only_settings_are_in_example(self) -> None:
        missing = ENV_ONLY - _example_vars()
        assert not missing, (
            f"в .env.example нет переменных: {sorted(missing)}. "
            "У них нет формы в интерфейсе, и шаблон — единственное место, "
            "где о них можно узнать"
        )

    def test_no_unknown_variables(self) -> None:
        extra = _example_vars() - {s.env for s in SETTINGS} - ENV_ONLY
        assert not extra, (
            f"в .env.example есть лишние переменные: {sorted(extra)}. "
            "Приложение их не читает, и заполнивший их будет ждать эффекта зря"
        )


def test_healthcheck_does_not_use_curl() -> None:
    """curl удалён из образа — healthcheck на нём считал бы контейнер мёртвым.

    Комментарии пропускаем: в них слово curl как раз объясняет, почему его тут
    нет. Проверять надо команду, а не пояснение к ней.
    """
    text = COMPOSE.read_text(encoding="utf-8")
    block = text[text.index("healthcheck:") :].split("logging:")[0]
    assert "curl" not in "\n".join(_lines_without_comments(block))
