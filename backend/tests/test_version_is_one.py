"""Номер версии обязан быть одним и тем же во всех пяти местах.

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
* `docker-compose.yml` — тег локально собираемого образа.
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
    "docker-compose.yml": r"image:\s*anime365-downloader:(?P<ver>\S+)",
}


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
