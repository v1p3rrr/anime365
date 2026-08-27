"""Номер версии обязан быть одним и тем же во всех семи местах.

Раньше это была мелкая неаккуратность: разъехались бы `/api/health` и подпись
перед Jellyfin — неприятно, но не смертельно. С появлением сборочного пайплайна
(`.github/workflows/ci.yml`) это стало вопросом того, можно ли вообще верить
номеру на образе.

Пайплайн берёт версию **из одного места** — `backend/pyproject.toml` — и клеит
её на образ: `0.1.0-build.42`. Дальше есть ровно один способ узнать, что за
образ крутится на сервере: спросить `/api/health`. Если там свой, отставший
номер, ответ будет уверенным и неправильным, а это хуже, чем никакого.

Отдельно — тег `vX.Y.Z`: работа publish отказывается собирать релиз, если тег
не совпал с версией в pyproject. Тест страхует ту же связку с другой стороны.

Проверяется:

* `backend/pyproject.toml` — источник, откуда берёт CI;
* `backend/app/main.py` — версия, которую FastAPI показывает в `/api/docs`;
* `backend/app/api/health.py` — то, что отдаётся в `/api/health`;
* `backend/app/services/jellyfin.py` — чем подписываемся перед Jellyfin;
* `docker-compose.yml` — тег локально собираемого образа;
* `frontend/package.json` — версия пакета фронтенда;
* `frontend/src/App.tsx` — **бейдж в шапке**, единственный номер, который видит
  человек, не заходя в `/api/health`.

⚠️ Две последние строки дописаны ревизией фронтенда. Сторож существовал и
перечислял пять мест, а номер лежал в семи: фронтенд остался снаружи. То есть
после первого же подъёма версии в шапке приложения стояла бы старая — с той же
уверенностью, ради которой этот тест и заведён.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PYPROJECT = REPO / "backend" / "pyproject.toml"

#: Где ещё лежит номер и каким выражением он оттуда достаётся. Группа `ver` —
#: сам номер.
PLACES: dict[str, str] = {
    "backend/app/main.py": r'version="(?P<ver>[^"]+)"',
    "backend/app/api/health.py": r'"version":\s*"(?P<ver>[^"]+)"',
    "backend/app/services/jellyfin.py": r'APP_VERSION\s*=\s*"(?P<ver>[^"]+)"',
    "frontend/package.json": r'"version":\s*"(?P<ver>[^"]+)"',
    # Бейдж в шапке. Литерал, а не вычисляемое значение: собирать его из
    # package.json пришлось бы через настройку сборщика ради одной подписи, а
    # так номер виден там же, где написан.
    "frontend/src/App.tsx": r"\bv(?P<ver>\d+\.\d+\.\d+)\b",
}

#: Строка образа в compose. Имя может быть с пространством имён реестра
#: (`логин/anime365-downloader`), а тег — каким угодно, см. тест ниже.
COMPOSE_IMAGE = r"image:\s*(?:\S+/)?anime365-downloader:(?P<ver>\S+)"


def _declared() -> str:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["version"]


def _found(relative: str, pattern: str) -> str:
    text = (REPO / relative).read_text(encoding="utf-8")
    match = re.search(pattern, text)
    assert match, f"в {relative} не нашёл версию по выражению {pattern!r}"
    return match.group("ver")


def test_version_matches_everywhere():
    expected = _declared()
    mismatched = {
        relative: found
        for relative, pattern in PLACES.items()
        if (found := _found(relative, pattern)) != expected
    }
    assert not mismatched, (
        f"в backend/pyproject.toml версия {expected}, а разошлись: {mismatched}. "
        "Пайплайн клеит на образ версию из pyproject — остальные места обязаны "
        "говорить то же самое, иначе по /api/health не понять, что запущено"
    )


def test_version_looks_like_a_number():
    """Иначе тег образа получится вида `${VERSION}-build.7` и ничего не значит."""
    version = _declared()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"версия {version!r} не похожа на X.Y.Z; из неё собирается тег образа "
        "и проверка тега vX.Y.Z в работе publish"
    )


def test_compose_tag_is_consistent():
    """Тег в `docker-compose.yml` — только если он вообще номер версии.

    Пока образ собирается на месте (`build: .`), тег там — номер версии, и он
    обязан совпадать с остальными. Но compose может показывать и на реестр:
    тогда осмысленные теги — `latest` или `0.1.0-build.7`, которые клеит
    пайплайн, и сверять их с `pyproject.toml` бессмысленно. В этом случае за
    номер отвечает не файл, а сама сборка ([D-161](../../claude/09-decisions.md)).

    Поэтому проверка срабатывает, только когда тег выглядит как X.Y.Z. Что
    строка образа вообще на месте — проверяется всегда.
    """
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(COMPOSE_IMAGE, text)
    assert match, "в docker-compose.yml не нашёл строку с образом anime365-downloader"

    tag = match.group("ver")
    if not re.fullmatch(r"\d+\.\d+\.\d+", tag):
        return  # образ берётся из реестра — номер клеит пайплайн, не файл

    assert tag == _declared(), (
        f"в docker-compose.yml собирается образ {tag}, а в pyproject.toml версия "
        f"{_declared()}"
    )
