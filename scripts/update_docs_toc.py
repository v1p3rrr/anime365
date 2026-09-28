#!/usr/bin/env python3
"""Пересобирает оглавления с номерами строк в документации проекта.

Зачем это нужно: файлы в `claude/` и `docs/` большие (самый длинный — три с
половиной тысячи строк), и читать их целиком ради одного раздела дорого.
Поэтому у каждого файла сверху лежит сводка «зачем он» и таблица
«строка → заголовок»: сначала читается верх, потом сразу нужный кусок.

Номера строк устаревают при первой же правке текста, поэтому руками их не
ведут — их пересчитывает этот скрипт.

Как это устроено. В файле стоят маркеры

    <!-- TOC -->
    ...всё, что между ними, переписывается...
    <!-- /TOC -->

Место маркеров скрипт не выбирает и не создаёт: где поставили, там оглавление
и будет. Глубина задаётся в самом маркере: `<!-- TOC depth=2 -->` возьмёт
только `##`, по умолчанию берутся `##` и `###`.

Запуск:

    python scripts/update_docs_toc.py            # обновить всё
    python scripts/update_docs_toc.py --check    # только проверить (код 1, если устарело)
    python scripts/update_docs_toc.py claude/09-decisions.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Где искать документацию. Каталоги сборки и кэши сюда не попадают намеренно.
DEFAULT_GLOBS = ("*.md", "claude/*.md", "docs/*.md")

# Кому оглавление не положено. `CLAUDE.md` целиком уезжает в контекст ассистента
# при каждом запуске — оглавление там было бы лишним весом, а не навигацией.
# `LICENSE.md` — юридический текст, его не читают кусками и не правят.
NO_TOC = {"CLAUDE.md", "LICENSE.md"}

OPEN_RE = re.compile(r"^<!--\s*TOC\b(?P<attrs>[^>]*?)-->\s*$")
CLOSE_RE = re.compile(r"^<!--\s*/TOC\s*-->\s*$")
DEPTH_RE = re.compile(r"depth\s*=\s*(\d)")
HEADING_RE = re.compile(r"^(?P<hashes>#{2,6})\s+(?P<text>\S.*?)\s*#*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")

DEFAULT_DEPTH = 3
MAX_PASSES = 10


class TocError(RuntimeError):
    pass


def find_markers(lines: list[str]) -> tuple[int, int, int]:
    """Возвращает (индекс открывающего маркера, индекс закрывающего, глубина)."""
    start = end = None
    depth = DEFAULT_DEPTH
    for i, line in enumerate(lines):
        opened = OPEN_RE.match(line)
        if opened is not None:
            if start is not None:
                raise TocError("маркер <!-- TOC --> встречается дважды")
            start = i
            found = DEPTH_RE.search(opened.group("attrs"))
            if found is not None:
                depth = int(found.group(1))
        elif CLOSE_RE.match(line) is not None:
            if start is None:
                raise TocError("<!-- /TOC --> стоит раньше <!-- TOC -->")
            if end is not None:
                raise TocError("маркер <!-- /TOC --> встречается дважды")
            end = i
    if start is None:
        raise TocError("нет маркера <!-- TOC -->")
    if end is None:
        raise TocError("нет маркера <!-- /TOC -->")
    return start, end, depth


def collect_headings(lines: list[str], depth: int) -> list[tuple[int, int, str]]:
    """Заголовки документа как (номер строки с 1, уровень, текст).

    Заголовки внутри блоков кода пропускаются: в yaml и sh комментарий с решёткой
    выглядит как заголовок.
    """
    out: list[tuple[int, int, str]] = []
    fence: str | None = None
    for i, line in enumerate(lines, start=1):
        fenced = FENCE_RE.match(line)
        if fenced is not None:
            marker = fenced.group(1)
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        heading = HEADING_RE.match(line)
        if heading is None:
            continue
        level = len(heading.group("hashes"))
        if level > depth:
            continue
        out.append((i, level, heading.group("text").strip()))
    return out


def anchor(text: str, seen: dict[str, int]) -> str:
    """Якорь в том виде, в каком его делает GitHub: нижний регистр, пробелы в
    дефисы, пунктуация выброшена. Повторы получают суффикс `-1`, `-2`."""
    slug = text.lower()
    slug = re.sub(r"[`*_~]", "", slug)
    slug = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", slug)  # ссылка → её текст
    slug = re.sub(r"[^\w\s-]", "", slug, flags=re.UNICODE)
    # Пробелы в дефисы — без обрезки по краям: у заголовка, начинающегося со
    # значка («## 🔴 Правило №0…»), GitHub оставляет ведущий дефис, и якорь без
    # него никуда не ведёт.
    slug = slug.replace(" ", "-")
    count = seen.get(slug, 0)
    seen[slug] = count + 1
    return slug if count == 0 else f"{slug}-{count}"


def render(headings: list[tuple[int, int, str]], total: int) -> list[str]:
    block = [
        "<!-- Таблицу ниже собирает scripts/update_docs_toc.py — руками не править. -->",
        "",
        f"Всего строк: {total}.",
        "",
        "| Стр. | Раздел |",
        "|---:|---|",
    ]
    seen: dict[str, int] = {}
    for number, level, text in headings:
        # Скобки в подписи ссылки экранируются: в заголовках встречается
        # «[Oshi no Ko]», и без экранирования ссылка разваливается.
        title = text.replace("|", r"\|").replace("[", r"\[").replace("]", r"\]")
        block.append(f"| {number} | {'#' * level} [{title}](#{anchor(text, seen)}) |")
    block.append("")
    return block


def rebuild(text: str) -> str:
    lines = text.split("\n")
    start, end, depth = find_markers(lines)
    before, after = lines[: start + 1], lines[end:]

    inner: list[str] = []
    for _ in range(MAX_PASSES):
        document = before + inner + after
        # Заголовки самого блока оглавления в него же попасть не могут: там строки
        # таблицы, а не заголовки. Но на всякий случай считаем по всему документу —
        # так номера гарантированно совпадают с тем, что увидит читатель.
        fresh = render(collect_headings(document, depth), len(document))
        if fresh == inner:
            return "\n".join(document)
        inner = fresh
    raise TocError("оглавление не сошлось за 10 проходов — похоже на ошибку в скрипте")


def documents() -> list[Path]:
    """Файлы, у которых оглавление должно быть.

    Тот же список сторожит `backend/tests/test_docs_toc.py`, поэтому он живёт
    здесь: два независимых перечисления разошлись бы, и новый файл документации
    молча остался бы без оглавления.
    """
    found: list[Path] = []
    for pattern in DEFAULT_GLOBS:
        found.extend(p for p in sorted(ROOT.glob(pattern)) if p.name not in NO_TOC)
    return found


def targets(argv: list[str]) -> list[Path]:
    if argv:
        return [Path(a) if Path(a).is_absolute() else ROOT / a for a in argv]
    return documents()


def main() -> int:
    # Консоль Windows по умолчанию cp1252, и печать русских имён файлов роняет
    # скрипт после того, как файлы уже переписаны — то есть отказ выглядит
    # страшнее, чем есть.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="файлы; по умолчанию вся документация")
    parser.add_argument(
        "--check",
        action="store_true",
        help="ничего не писать, вернуть 1, если оглавление устарело",
    )
    args = parser.parse_args()

    stale: list[str] = []
    changed: list[str] = []
    for path in targets(args.files):
        text = path.read_text(encoding="utf-8")
        if "<!-- TOC" not in text:
            continue
        name = path.relative_to(ROOT).as_posix()
        try:
            updated = rebuild(text)
        except TocError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            return 2
        if updated == text:
            continue
        if args.check:
            stale.append(name)
            continue
        path.write_text(updated, encoding="utf-8", newline="\n")
        changed.append(name)

    if args.check:
        for name in stale:
            print(f"устарело оглавление: {name}")
        if stale:
            print("\nЗапустите: python scripts/update_docs_toc.py")
            return 1
        print("оглавления актуальны")
        return 0

    for name in changed:
        print(f"обновлено: {name}")
    if not changed:
        print("изменений нет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
