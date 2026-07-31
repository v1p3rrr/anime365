"""Список зависимостей в Dockerfile обязан совпадать с pyproject.toml.

Дубль там сделан ради кэша слоёв, и это разумно. Но однажды списки разошлись, и
разошлись тихо: в образе не оказалось `socksio`, из-за чего поддержка
SOCKS5-прокси не работала вовсе — при том, что она описана в документации и
объявлена в pyproject. Обнаружилось это спустя долгое время и случайно.

Тест дешёвый, а ловит ровно тот класс расхождений, который иначе виден только
в работающем контейнере.
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


def _dockerfile_deps() -> set[str]:
    """Пакеты из блоков `RUN pip install` — они перечислены в кавычках.

    Блоков несколько: отдельным слоем обновляется сам pip (у старой версии свои
    CVE, а из образа он никуда не девается). Раньше разбирался только первый
    найденный блок, и добавление слоя с pip увело проверку не туда — она стала
    сверять список зависимостей с одним лишь `pip==…`.

    Сам pip из сравнения исключаем: он не зависимость приложения и в pyproject
    ему не место.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    blocks = re.findall(r"RUN pip install[^\n]*((?:.|\n)*?)\n\n", text)
    assert blocks, "не нашёл блок установки зависимостей в Dockerfile"
    found = {name for block in blocks for name in re.findall(r'"([^"]+)"', block)}
    return {name for name in found if not name.startswith("pip==")}


def test_dockerfile_installs_exactly_what_pyproject_declares():
    expected = _pyproject_deps()
    actual = _dockerfile_deps()

    missing = expected - actual
    extra = actual - expected

    assert not missing, (
        "В Dockerfile не хватает зависимостей, объявленных в pyproject.toml: "
        f"{sorted(missing)}. В образе их не будет, и приложение упадёт при импорте "
        "или тихо потеряет возможность (так было с socksio и SOCKS5-прокси)."
    )
    assert not extra, (
        f"В Dockerfile есть лишнее против pyproject.toml: {sorted(extra)}. "
        "Объявлять зависимость надо в pyproject, иначе про неё не узнает ни "
        "локальная разработка, ни pip-audit."
    )


def test_extras_are_not_lost():
    """Отдельно про `[extras]` — именно на них и произошло расхождение.

    `httpx[http2,socks]` против `httpx[http2]` — строки разные, но глазами при
    беглом чтении неотличимы, а последствие серьёзное: без `socks` httpx на
    `ALL_PROXY=socks5://…` падает на каждом запросе.
    """
    for dep in _pyproject_deps():
        if "[" not in dep:
            continue
        assert dep in _dockerfile_deps(), (
            f"{dep}: набор extras в Dockerfile не совпадает с pyproject.toml"
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
    сообщением, а VPN-сценарий из документации молча не работает.
    """
    import importlib.util

    assert importlib.util.find_spec("socksio") is not None, (
        "socksio отсутствует: SOCKS5-прокси работать не будет. "
        "Он приходит как httpx[socks] — проверьте extras в pyproject и Dockerfile."
    )


def test_python_version_matches():
    """Версия Python в образе и в требованиях проекта — одна.

    Иначе синтаксис, принятый локально, отваливается при сборке.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"FROM python:(\d+)\.(\d+)", text)
    assert match, "не нашёл базовый образ python в Dockerfile"
    major, minor = int(match.group(1)), int(match.group(2))
    assert (major, minor) == sys.version_info[:2], (
        f"Dockerfile собирает на Python {major}.{minor}, "
        f"а тесты идут на {sys.version_info.major}.{sys.version_info.minor}"
    )
