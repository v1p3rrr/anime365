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
    # Healthcheck докера. Закрыть его значит объявить контейнер мёртвым.
    "health.health",
    # Кто я и что мне можно — этим интерфейс решает, что показывать.
    "users.me",
    # Поток событий: только уведомления о прогрессе, содержимого в них нет.
    "events.events",
    # Каталог — публичная часть: он и так виден на сайте Anime365.
    "catalog.catalog_ids",
    "catalog.catalog_cards",
    "catalog.catalog_genres",
    "catalog.search_online",
    "catalog.catalog_status",
    # Страница тайтла и всё, что её наполняет. Чтение чужого каталога.
    "title.title_page",
    "title.translation_embed",
    "title.series_relations",
    "title.series_details",
    "title.studio_logo_image",
    "title.series_jellyfin_link",
    "title.series_audio",
    # Чтение состояния библиотеки: показывается на общих страницах, ничего не
    # меняет и путей наружу не отдаёт сверх того, что и так видно в интерфейсе.
    "library.library_status",
    "library.unmapped_folders",
    "library.unmatched_files",
    "library.jellyfin_audit",
    "library.audio_library",
    "library.list_jellyfin_collections",
    "library.list_mappings",
    "library.series_files",
    "library.list_episode_targets",
    "library.episode_target_folders",
    "library.mapping_report",
    # Сухой прогон парсера имён: ничего не пишет, принимает только строки.
    "library.parse_test",
    # Список задач очереди — та же очередь, что видна на странице загрузок.
    "downloads.list_tasks",
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


def test_mutating_routes_are_never_open() -> None:
    """Отдельно и жёстко: изменяющий метод открытым быть не может.

    Именно здесь была дыра — батч загрузок ставился без права DOWNLOADS.
    """
    allowed = {"auth.login", "auth.logout", "library.parse_test", "catalog.catalog_cards"}
    offenders = [
        f"{module}.{name}"
        for module, name, method, args in _routes()
        if method in {"POST", "PUT", "DELETE", "PATCH"}
        and not GUARD_RE.search(args)
        and f"{module}.{name}" not in allowed
    ]
    assert not offenders, f"изменяющие маршруты без права: {offenders}"
