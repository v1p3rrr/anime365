"""Внутренние ссылки интерфейса должны вести на существующие маршруты.

⚠️ Тест появился после живой ошибки. В списке «Тайтлы со своей настройкой» и в
очереди ненайденных субтитров ссылка строилась как `/title/{id}`, а маршрут в
`routes.tsx` называется `series/:id`. Клик открывал экран ошибки: компилятор
такое не ловит — для него это обычная строка, — а руками проверяются не все
списки подряд.

Разбор регулярками, а не парсером TypeScript: тащить в тесты node ради двух
файлов дороже, чем описать их форму. Форма стабильна — маршруты объявляются
одним `createBrowserRouter`, ссылки строятся шаблонными строками.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
ROUTES = SRC / "routes.tsx"

#: `to={`/series/${id}`}` и `to="/queue"` — оба вида записи.
LINK = re.compile(r"""to=(?:\{`(/[^`$]*)(?:\$\{[^}]*\})?[^`]*`\}|["'](/[^"']*)["'])""")


def _declared_routes() -> set[str]:
    """Первый сегмент каждого объявленного маршрута плюс корень."""
    source = ROUTES.read_text(encoding="utf-8")
    found = {"/"}
    for match in re.finditer(r"path: '([^']+)'", source):
        path = match.group(1).lstrip("/")
        if path:
            found.add("/" + path.split("/")[0])
    return found


def _used_links() -> dict[str, list[str]]:
    """Куда ссылается интерфейс: первый сегмент → файлы, где встретился."""
    used: dict[str, list[str]] = {}
    for file in sorted(SRC.rglob("*.tsx")):
        for match in LINK.finditer(file.read_text(encoding="utf-8")):
            target = match.group(1) or match.group(2)
            head = "/" + target.strip("/").split("/")[0]
            used.setdefault(head or "/", []).append(str(file.relative_to(SRC)))
    return used


def test_every_internal_link_has_a_route() -> None:
    declared = _declared_routes()
    unknown = {
        target: files for target, files in _used_links().items() if target not in declared
    }
    assert not unknown, (
        f"ссылки ведут на несуществующие маршруты: {unknown}; "
        f"объявлены {sorted(declared)}"
    )


def test_the_title_page_lives_at_series() -> None:
    """Отдельно и дословно: именно на этом обожглись."""
    assert "/series" in _declared_routes()
    assert "/title" not in _used_links()
