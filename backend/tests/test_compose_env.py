"""`docker-compose.yml` обязан перечислять все настройки из реестра.

Пользователь попросил объявлять переменные в compose явно, со ссылками на
`.env`, — «чтобы более явно это всё было». Смысл в том, что список того, чем
управляют, лежит в репозитории и виден в одном месте, а значения остаются в
`.env`, который не коммитится.

Цена такого дубля — он разъезжается. Ровно это уже случалось со списком
зависимостей в Dockerfile: в образе не оказалось socksio, и SOCKS5-прокси не
работал вовсе, хотя был описан в документации. Поэтому здесь тест.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.settings_registry import SETTINGS

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _declared() -> set[str]:
    text = COMPOSE.read_text(encoding="utf-8")
    return set(re.findall(r"^\s*- (A365_[A-Z0-9_]+)=", text, re.MULTILINE))


def test_every_setting_is_declared() -> None:
    missing = {s.env for s in SETTINGS} - _declared()
    assert not missing, (
        f"в docker-compose.yml нет переменных: {sorted(missing)}. "
        "Настройку добавили в реестр, но управлять ей из compose нельзя — "
        "а именно ради этого список там и держится"
    )


# Настройки, которых нет в реестре: они меняются только через окружение, и
# формы в интерфейсе у них нет. В compose перечислены наравне с остальными,
# иначе про них никто не узнает.
ENV_ONLY = {
    "A365_TRANSLATION_SCAN_HOURS",
    "A365_TRANSLATION_SCAN_RPM",
    "A365_TRANSLATION_SCAN_SCOPE",
}


def test_no_unknown_variables() -> None:
    extra = _declared() - {s.env for s in SETTINGS} - ENV_ONLY
    assert not extra, (
        f"в docker-compose.yml есть лишние переменные: {sorted(extra)}. "
        "Приложение их не читает, и заполнивший их будет ждать эффекта зря"
    )


def test_values_come_from_env_file() -> None:
    """Значения должны быть ссылками, а не вписанными строками.

    Вписанное значение — это секрет в репозитории, если речь о токене, и
    расхождение с `.env` во всех остальных случаях.
    """
    text = COMPOSE.read_text(encoding="utf-8")
    for name, value in re.findall(r"^\s*- (A365_[A-Z0-9_]+)=(.*)$", text, re.MULTILINE):
        value = value.split("#")[0].strip()
        assert value == "${%s:-}" % name, (
            f"{name} задан значением {value!r}, а должен ссылаться на .env"
        )


def test_env_file_is_not_mixed_with_interpolation() -> None:
    """`env_file:` и `${VAR}` в `environment:` нельзя смешивать.

    Проверено на живом compose: подстановка `${VAR}` берёт значения только из
    окружения оболочки и из `.env`, файл из `env_file:` для неё не существует.
    А `environment:` имеет приоритет над `env_file:` — поэтому строка
    `- A365_ACCESS_TOKEN=${A365_ACCESS_TOKEN:-}` подставляла пустую строку и
    затирала значение из подключённого файла.

    Снаружи это выглядело как «часть настроек из .env не подтягивается»: на
    деле не подтягивалась ни одна, а работали значения по умолчанию.
    """
    text = COMPOSE.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not any(line.strip().startswith("env_file:") for line in lines), (
        "env_file: вернулся в compose вместе со списком ${VAR} в environment: — "
        "эта пара молча обнуляет все настройки"
    )


def test_healthcheck_does_not_use_curl() -> None:
    """curl удалён из образа — healthcheck на нём считал бы контейнер мёртвым.

    Комментарии пропускаем: в них слово curl как раз объясняет, почему его тут
    нет. Проверять надо команду, а не пояснение к ней.
    """
    text = COMPOSE.read_text(encoding="utf-8")
    block = text[text.index("healthcheck:") :].split("logging:")[0]
    command = [line for line in block.splitlines() if not line.lstrip().startswith("#")]
    assert "curl" not in "\n".join(command)
