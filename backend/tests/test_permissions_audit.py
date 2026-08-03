"""Каждый изменяющий эндпоинт обязан спрашивать право.

Найдено проверкой, а не придумано: пользователь с одним лишь правом «смотреть»
успешно прочитал `/api/settings`, поставил батч загрузок и получил видео через
прокси. Роль «пусть смотрит, но ничего не качает и не меняет» при этом не
значила ничего.

Тест смотрит на объявления маршрутов, а не на поведение: перебрать все
сочетания прав вживую дорого, а забытая зависимость видна прямо в декораторе.
"""

from __future__ import annotations

import re
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "app" / "api"

#: Маршруты, которым право не нужно, и почему. Список закрытый: всё, чего в нём
#: нет, обязано требовать право. Так забытая проверка становится падением
#: теста, а не тихой дырой.
OPEN_ROUTES = {
    # Вход и выход — до всякой авторизации по определению.
    "auth.auth_status",
    "auth.login",
    "auth.logout",
    # Healthcheck докера. Закрыть его значит объявить контейнер мёртвым: он
    # ходит без куки. Ничего, кроме «жив/не жив» и версии, не отдаёт.
    "health.health",
    # «Кто я и что мне можно» — этим интерфейс решает, что вообще показывать.
    # Требовать для него право нельзя: тогда клиент не смог бы узнать, что прав
    # у него нет, и показал бы пустой экран без объяснения.
    "users.me",
}

#: Обе формы проверки: по конкретному праву и «только администратор».
GUARD_RE = re.compile(r"require(_admin)?\(")


ROUTE_RE = re.compile(
    r"@router\.(get|post|put|delete|patch)\((?P<args>.*?)\)\s*\n(?:@router\.[a-z]+\(.*?\)\s*\n)*(?:async )?def (?P<name>\w+)",
    re.S,
)


def _routes() -> list[tuple[str, str, str, str]]:
    found = []
    for path in sorted(API_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in ROUTE_RE.finditer(text):
            found.append(
                (path.stem, match.group("name"), match.group(1).upper(), match.group("args"))
            )
    return found


def test_every_route_is_classified() -> None:
    """Новый маршрут обязан быть либо защищён, либо назван открытым явно."""
    unguarded = [
        f"{module}.{name}"
        for module, name, _, args in _routes()
        if not GUARD_RE.search(args)
    ]
    forgotten = sorted(set(unguarded) - OPEN_ROUTES)
    assert not forgotten, (
        f"маршруты без проверки прав: {forgotten}. Добавьте "
        "dependencies=[Depends(require(Permission.…))] или впишите в OPEN_ROUTES "
        "с объяснением, почему им право не нужно"
    )


def test_open_list_has_no_stale_entries() -> None:
    """Список открытых не должен разрастаться незаметно."""
    unguarded = {
        f"{module}.{name}"
        for module, name, _, args in _routes()
        if not GUARD_RE.search(args)
    }
    stale = sorted(OPEN_ROUTES - unguarded)
    assert not stale, (
        f"в списке открытых маршрутов лишние записи: {stale}. Они уже защищены "
        "или удалены — уберите их, иначе список перестанет что-либо значить"
    )


def test_every_registered_route_is_guarded() -> None:
    """То же самое, но по собранному приложению, а не по тексту файлов.

    Разбор декораторов видит только `app/api/*.py`. Маршрут, объявленный в
    другом месте — например `/api/docs` прямо в `main.py`, — прошёл бы мимо
    него незамеченным. Здесь смотрим на то, что реально зарегистрировано, и
    заодно ловим случай, когда зависимость объявлена, но не той формой.
    """
    from app.main import app

    def guarded(route) -> bool:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            return False
        stack = list(dependant.dependencies)
        while stack:
            dep = stack.pop()
            name = getattr(dep.call, "__qualname__", "")
            if name.startswith(("require.", "require_admin.")):
                return True
            stack.extend(dep.dependencies)
        return False

    #: Маршруты приложения, которым право не нужно. Ключ — путь, а не имя
    #: функции: у SPA-фолбека и статики имён в терминах API нет.
    open_paths = {
        "/api/health",
        "/api/auth/status",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/users/me",
    }

    offenders = sorted(
        f"{sorted(m for m in route.methods if m not in {'HEAD', 'OPTIONS'})} {route.path}"
        for route in app.routes
        if getattr(route, "methods", None)
        and route.path.startswith("/api")
        and route.path not in open_paths
        and not guarded(route)
    )
    assert not offenders, f"зарегистрированные маршруты без права: {offenders}"


def test_mutating_routes_are_never_open() -> None:
    """Отдельно и жёстко: изменяющий метод открытым быть не может.

    Именно здесь была дыра — батч загрузок ставился без права DOWNLOADS.
    """
    # Только вход и выход: они по определению работают до всякой авторизации.
    # Раньше здесь были ещё `library.parse_test` и `catalog.catalog_cards` —
    # оба теперь спрашивают право, и записи стали пустыми словами.
    allowed = {"auth.login", "auth.logout"}
    offenders = [
        f"{module}.{name}"
        for module, name, method, args in _routes()
        if method in {"POST", "PUT", "DELETE", "PATCH"}
        and not GUARD_RE.search(args)
        and f"{module}.{name}" not in allowed
    ]
    assert not offenders, f"изменяющие маршруты без права: {offenders}"
