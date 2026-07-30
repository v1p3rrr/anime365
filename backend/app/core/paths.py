"""Проверка путей записи.

Ниже по коду имена файлов уже санитизируются (`naming.sanitize_component`), но
это защита на уровне «отдельный компонент имени». Здесь — независимая проверка
итогового пути: что бы ни собралось из названия тайтла, имени автора перевода
и содержимого БД, запись обязана происходить внутри настроенного корня.

Имя автора перевода приходит из внешнего API, то есть это недоверенный ввод,
который попадает в имя файла. Одной санитизации мало: между ней и записью
лежит склейка путей, а ошибка в ней не должна давать запись куда попало.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


class UnsafePathError(ValueError):
    """Путь ведёт за пределы разрешённого корня."""


def resolve_within(root: str | Path, candidate: str | Path) -> Path:
    """Возвращает абсолютный путь `candidate`, убедившись, что он внутри `root`.

    Разыменование симлинков делается ДО сравнения: иначе ссылка внутри корня,
    указывающая наружу, проходит проверку и уводит запись за пределы библиотеки.
    """
    root_path = Path(root)
    target = Path(candidate)
    if not target.is_absolute():
        target = root_path / target

    # strict=False: целевого файла ещё нет, разрешаются только существующие
    # части пути — этого достаточно, симлинки лежат в существующих каталогах.
    resolved_root = root_path.resolve(strict=False)
    resolved_target = target.resolve(strict=False)

    if resolved_target != resolved_root and not resolved_target.is_relative_to(resolved_root):
        raise UnsafePathError(
            f"Путь {resolved_target} находится вне разрешённого корня {resolved_root}"
        )
    return resolved_target


def is_within(root: str | Path, candidate: str | Path) -> bool:
    try:
        resolve_within(root, candidate)
    except UnsafePathError:
        return False
    return True
