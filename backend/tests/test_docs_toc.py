"""Оглавления в документации должны совпадать с самой документацией.

Зачем сторож. Файлы в `claude/` и `docs/` большие — самый длинный на три с
половиной тысячи строк, — и читать их целиком ради одного раздела дорого.
Поэтому сверху у каждого лежит таблица «строка → заголовок»: читается верх,
дальше сразу нужный кусок.

Номера строк в такой таблице устаревают при первой же правке текста, причём
**молча**: оглавление продолжает выглядеть правдоподобно и уводит не туда. Это
хуже, чем его отсутствие. Пересчитывает номера `scripts/update_docs_toc.py`, а
этот тест следит, чтобы его не забыли запустить.

Упал — выполните:

    python scripts/update_docs_toc.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "update_docs_toc.py"

# Скрипт лежит вне пакета приложения, поэтому подключается по пути, а не
# импортом: заводить ради него пакет и добавлять в PYTHONPATH было бы дороже.
_spec = importlib.util.spec_from_file_location("update_docs_toc", SCRIPT)
assert _spec is not None and _spec.loader is not None
_toc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_toc)


DOCUMENTS = _toc.documents()


def test_every_document_has_a_toc() -> None:
    """Новый файл документации без оглавления — тоже отклонение.

    Иначе правило «читаем верх, потом нужный кусок» держится ровно до первого
    файла, который его не соблюдает.
    """
    without = [
        path.relative_to(ROOT).as_posix()
        for path in DOCUMENTS
        if "<!-- TOC" not in path.read_text(encoding="utf-8")
    ]
    assert not without, (
        "нет маркеров <!-- TOC --> и <!-- /TOC --> в: "
        + ", ".join(sorted(without))
        + "; поставьте их подряд после вводного блока и запустите "
        "python scripts/update_docs_toc.py"
    )


@pytest.mark.parametrize(
    "document", DOCUMENTS, ids=[p.relative_to(ROOT).as_posix() for p in DOCUMENTS]
)
def test_toc_matches_the_document(document: Path) -> None:
    text = document.read_text(encoding="utf-8")
    assert _toc.rebuild(text) == text, (
        f"оглавление {document.relative_to(ROOT).as_posix()} разошлось с текстом; "
        "запустите: python scripts/update_docs_toc.py"
    )


def test_anchors_lead_somewhere() -> None:
    """Ссылки вида `](#…)` внутри файла обязаны попадать в его заголовок.

    Битый якорь молча ведёт в начало страницы, и заметить это можно только
    кликнув. Проверяются **все** внутренние ссылки, а не только сгенерированные:
    на них ссылаются решения друг из друга, и коротких форм вроде `#d-70` уже
    хватало, чтобы ссылка никуда не вела.
    """
    import re

    broken: list[str] = []
    for document in DOCUMENTS:
        lines = document.read_text(encoding="utf-8").split("\n")
        seen: dict[str, int] = {}
        anchors = {
            _toc.anchor(text, seen) for _, _, text in _toc.collect_headings(lines, 6)
        }
        name = document.relative_to(ROOT).as_posix()
        fence: str | None = None
        for number, line in enumerate(lines, start=1):
            fenced = _toc.FENCE_RE.match(line)
            if fenced is not None:
                marker = fenced.group(1)
                fence = marker if fence is None else (None if fence == marker else fence)
                continue
            if fence is not None:
                continue
            # Код — не ссылка. Иначе документация про саму эту проверку ломает
            # её же: строка вида `](#…)` в бэктиках выглядит как битый якорь.
            for link in re.findall(r"\]\(#([^)]+)\)", re.sub(r"`[^`]*`", "", line)):
                if link not in anchors:
                    broken.append(f"{name}:{number} → #{link}")
    assert not broken, "ссылки ведут в никуда:\n" + "\n".join(broken)
