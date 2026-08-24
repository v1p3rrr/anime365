"""`.dockerignore` не должен выкидывать из контекста то, что нужно сборке.

Файл появился ради двух вещей сразу. Первая — вес: без него `docker build .`
отправлял демону `temp/` с настоящей библиотекой и `data/` с базой каталога,
гигабайты на каждую сборку. Вторая важнее — в контекст попадал `.env` с
токенами. В образ он не переносится, но лежать в контексте секретам незачем.

Опасность у такого файла ровно одна и обратная: перестараться. Достаточно
одного лишнего шаблона (`frontend/public/` вместо `frontend/public/libass/`,
`config/` вместо `config/*` с возвратом одного файла), чтобы из контекста
пропало то, что Dockerfile ждёт. Проявится это не здесь, а падением сборки в
CI — или, хуже, собранным образом без правил разбора имён: контейнер с ними и
падал при сканировании библиотеки.

Проверять настоящей сборкой дорого (ffmpeg, npm, минуты), поэтому тест
разбирает `.dockerignore` сам. Правила совпадения — те же, что у Docker:
шаблоны применяются по порядку, побеждает последний совпавший, `!` возвращает
файл обратно.

Список нужных файлов ниже — руками и намеренно. Обходить каталоги целиком
нельзя: под ними лежат `node_modules` и `__pycache__`, исключённые как раз по
делу, и тест сверял бы `.dockerignore` сам с собой.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "Dockerfile"
DOCKERIGNORE = REPO / ".dockerignore"

#: Что обязано доехать до сборки. По файлу-представителю на каждую причину:
#: манифесты и локи фронтенда, точка входа, статика из `public/` (не только
#: libass), скрипт `prebuild`, зависимости бэкенда, код и правила разбора имён.
REQUIRED = [
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/index.html",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
    "frontend/postcss.config.cjs",
    "frontend/src/main.tsx",
    "frontend/public/color-scheme.js",
    "frontend/scripts/copy-libass.mjs",
    "backend/pyproject.toml",
    "backend/app/main.py",
    "backend/app/core/config.py",
    "config/filename_rules.yaml",
]


def _patterns() -> list[str]:
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _to_regex(pattern: str) -> re.Pattern[str]:
    """Шаблон Docker в регулярное выражение.

    `**` — любое число сегментов пути, `*` и `?` через разделитель не
    перепрыгивают. Завершающий слэш ничего не значит: каталог и так закрывает
    всё, что под ним, — это делает `_excluded`.
    """
    body = ""
    index = 0
    stripped = pattern.rstrip("/")
    while index < len(stripped):
        if stripped.startswith("**", index):
            body += ".*"
            index += 2
        elif stripped[index] == "*":
            body += "[^/]*"
            index += 1
        elif stripped[index] == "?":
            body += "[^/]"
            index += 1
        else:
            body += re.escape(stripped[index])
            index += 1
    return re.compile(rf"^{body}$")


def _excluded(path: str, patterns: list[str]) -> bool:
    parts = path.split("/")
    # Путь и все его родители: шаблон каталога закрывает всё содержимое.
    prefixes = ["/".join(parts[: n + 1]) for n in range(len(parts))]
    verdict = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        regex = _to_regex(pattern[1:] if negated else pattern)
        if any(regex.match(prefix) for prefix in prefixes):
            verdict = not negated
    return verdict


def _copy_sources() -> list[str]:
    """Источники всех `COPY`, кроме тех, что берутся из предыдущего этапа."""
    sources: list[str] = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("COPY "):
            continue
        parts = line.split()[1:]
        if any(part.startswith("--from=") for part in parts):
            continue
        # Последний аргумент — назначение внутри образа.
        sources.extend(part.rstrip("*") for part in parts[:-1])
    assert sources, "не нашёл ни одного COPY в Dockerfile"
    return sources


def test_copy_sources_are_not_excluded_wholesale():
    """Ни один источник `COPY` не закрыт целиком.

    Ловит грубый случай — исключённый `frontend/` или `config/`, — когда сборка
    падает на первой же инструкции.
    """
    patterns = _patterns()
    lost = [source for source in _copy_sources() if _excluded(source, patterns)]
    assert not lost, f"Dockerfile копирует {lost}, а .dockerignore их не пропускает"


def test_copy_sources_exist_on_disk():
    """Иначе проверка выше молча сторожит несуществующие пути."""
    missing = [source for source in _copy_sources() if not (REPO / source).exists()]
    assert not missing, f"источники COPY из Dockerfile не найдены: {missing}"


def test_required_files_survive_the_filter():
    patterns = _patterns()
    missing = [name for name in REQUIRED if not (REPO / name).exists()]
    assert not missing, (
        f"файлы из списка нужных пропали из репозитория: {missing}. "
        "Если это переименование — поправьте список, он держится руками"
    )
    dropped = [name for name in REQUIRED if _excluded(name, patterns)]
    assert not dropped, f".dockerignore выкидывает нужное сборке: {dropped}"


def test_filename_rules_are_whitelisted_back():
    """`config/*` закрыт целиком, и ровно один файл возвращён обратно.

    Порядок строк тут значимый: `!config/filename_rules.yaml` обязан идти
    **после** `config/*`, иначе побеждает более поздний шаблон и правила разбора
    имён в образ не попадут.
    """
    patterns = _patterns()
    assert not _excluded("config/filename_rules.yaml", patterns)
    assert _excluded("config/settings.yaml", patterns)
    assert _excluded("config/session_secret", patterns)


def test_secrets_and_user_data_stay_out():
    patterns = _patterns()
    for path in (
        ".env",
        ".env.local",
        "config/settings.yaml",
        "data/app.sqlite3",
        "temp/Bocchi the Rock/S01E01.mkv",
        "backend/app/static/index.html",
        "frontend/node_modules/react/package.json",
        "backend/app/api/__pycache__/health.cpython-312.pyc",
    ):
        assert _excluded(path, patterns), f"{path} не должен уезжать в контекст сборки"
