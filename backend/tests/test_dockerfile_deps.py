"""Образ ставит зависимости из `pyproject.toml`, а не из списка рядом.

Раньше список был продублирован в `Dockerfile` — считалось, что иначе правка
исходников тянет переустановку всего. Дубль стоил дорого дважды.

Сперва списки разошлись молча: в образе не оказалось `socksio`, и поддержка
SOCKS5-прокси не работала в контейнере вообще — при том, что она объявлена в
`pyproject`, описана в `docs/admin.md` и была предметом отдельного решения.
Обнаружилось случайно, когда от новой зависимости упал импорт. Тогда завели этот
файл: он сверял два списка ([D-66](../../claude/09-decisions.md)).

Потом появился Dependabot. Он правит `pyproject.toml` и про второй список не
знает, поэтому **каждое** его обновление питоновских зависимостей падало здесь и
требовало ручной правки `Dockerfile`. Сверка работала, но чинить приходилось не
причину, а следствие.

Причина оказалась устаревшей: `pyproject.toml` копируется в образ **до**
установки и **до** исходников, так что слой ломается только при правке самого
`pyproject` — ровно тогда, когда зависимости и меняются. Дубль перестал что-либо
экономить ([D-167](../../claude/09-decisions.md)), и его больше нет.

Тесты ниже сторожат новое устройство: список в `Dockerfile` не должен появиться
снова, зависимости обязаны реально импортироваться, а версия Python в образе —
совпадать с той, на которой идут тесты.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "Dockerfile"
PYPROJECT = REPO / "backend" / "pyproject.toml"


def _pyproject_deps() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return set(data["project"]["dependencies"])


def _install_block() -> str:
    """Инструкция `RUN`, которая ставит зависимости приложения.

    Блоков с `pip install` в файле два: отдельным слоем обновляется сам pip (у
    старой версии свои CVE, а из образа он никуда не девается). Нас интересует
    тот, что работает со списком требований.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"RUN [^\n]*requirements\.txt(?:[^\n]*\\\n)*[^\n]*", text)
    assert match, "не нашёл в Dockerfile установку зависимостей по файлу требований"
    return match.group(0)


def test_dependencies_come_from_pyproject():
    """Образ читает `pyproject.toml` и ставит по нему, а не по списку рядом."""
    block = _install_block()
    assert "pyproject.toml" in block, (
        "установка зависимостей больше не читает pyproject.toml — значит "
        "где-то снова появился второй список, который разойдётся с первым"
    )
    assert re.search(r"pip install[^\n]*-r ", block), (
        "ожидается `pip install -r <файл требований>`, собранный из pyproject"
    )


def test_no_hardcoded_list_came_back():
    """В `Dockerfile` не должно быть пинов пакетов — кроме самого pip.

    Ровно это и было источником расхождения. Тест намеренно смотрит на форму
    `"имя==версия"`, а не на конкретные имена: вернуть список могут и по частям,
    «только одну зависимость, временно».
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    pins = set(re.findall(r'"([A-Za-z0-9_.\-]+(?:\[[^\]]+\])?==[^"]+)"', text))
    pins = {pin for pin in pins if not pin.startswith("pip==")}
    assert not pins, (
        f"в Dockerfile снова прибиты версии пакетов: {sorted(pins)}. "
        "Единственный источник — backend/pyproject.toml"
    )


def test_installed_environment_matches_too():
    """Локальное окружение тоже должно уметь всё, что объявлено.

    Иначе тесты зелёные, а в контейнере отказ — ровно то, что уже случалось.
    """
    import importlib.util

    # Имя пакета не всегда совпадает с именем дистрибутива.
    IMPORT_NAMES = {
        "iso639-lang": "iso639",
        "python-multipart": "multipart",
        "argon2-cffi": "argon2",
        "pyyaml": "yaml",
        "uvicorn[standard]": "uvicorn",
        "httpx[http2,socks]": "httpx",
        "apscheduler": "apscheduler",
    }

    missing = []
    for dep in _pyproject_deps():
        name = re.split(r"[=<>~!]", dep)[0]
        module = IMPORT_NAMES.get(name, name.replace("-", "_"))
        if importlib.util.find_spec(module) is None:
            missing.append(f"{name} (модуль {module})")

    assert not missing, f"Не установлено: {missing}. Выполните pip install -e '.[dev]'"


def test_socks_support_is_actually_available():
    """`socksio` ставится как extra httpx — проверяем сам факт, а не строку.

    Без него `ALL_PROXY=socks5://…` падает на первом же запросе с невнятным
    сообщением, а VPN-сценарий из документации молча не работает. Extras теперь
    приходят из pyproject как есть, но проверка остаётся: она про результат.
    """
    import importlib.util

    assert importlib.util.find_spec("socksio") is not None, (
        "socksio отсутствует: SOCKS5-прокси работать не будет. "
        "Он приходит как httpx[socks] — проверьте extras в pyproject."
    )


def test_python_version_matches():
    """Версия Python в образе и в требованиях проекта — одна.

    Иначе синтаксис, принятый локально, отваливается при сборке.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"FROM python:(\d+)\.(\d+)", text)
    assert match, "не нашёл базовый образ python в Dockerfile"

    image = (int(match.group(1)), int(match.group(2)))
    assert image == sys.version_info[:2], (
        f"образ собирается на Python {image[0]}.{image[1]}, "
        f"а тесты идут на {sys.version_info[0]}.{sys.version_info[1]}"
    )
