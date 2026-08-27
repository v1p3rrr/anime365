"""Транзакция не должна ждать чего-то медленного, пока держит базу.

SQLite допускает **одного** писателя. Значит любая открытая пишущая транзакция —
это очередь из всех остальных: загрузок, интерфейса, фоновых джоб. Пока она
жива, они стоят, а на десятой секунде получают «database is locked» (у нас
`busy_timeout=10000`).

Тест разбирает исходники и ищет два способа задержать транзакцию:

1. **`await` внутри `with session_scope()`** — самое опасное. Сетевой запрос
   длится секунды, и всё это время база занята. Ни разу не встречалось, и пусть
   так и остаётся: поймать это глазами на ревью почти невозможно.
2. **вложенные `session_scope()`** — вторая транзакция ждёт первую на том же
   потоке, а первая ждёт вторую. С одним соединением это тупик, с пулом —
   лишний захват лока.

Тест не про стиль. Оба случая уже стоили сервера: в логах пользователя лежат
падения загрузок и синка каталога ровно из-за долгих транзакций.

⚠️ **Чего сканер НЕ видит: вложенность через вызов.** Глубина считается
лексически, внутри одного тела функции, — а функция, вызванная из-под открытой
транзакции, может открыть свою, и для сканера это невидимо.

Проверено отдельным проходом по всем 672 функциям приложения: таких мест **два**,
и оба безобидны, потому что внутренняя транзакция только читает.

    download_queue.enqueue()  -> existing_audio_path()
    shikimori.get_relations() -> _resolve_to_our_catalog()

Опасен ровно один случай — **писатель внутри писателя**: у SQLite один писатель,
и внутренняя транзакция ждала бы внешнюю, которая не может завершиться, пока не
вернётся внутренняя. Сегодня такого нет. Расширять сканер до графа вызовов не
стали: он начал бы ругаться на эти два безобидных места, а отличать чтение от
записи по AST — гадание. Если правите одно из них так, что внутренняя начинает
писать, поймать это придётся глазами.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

#: Что считаем открытием транзакции.
SESSION_OPENERS = ("session_scope", "engine.begin")


def _is_session_ctx(item: ast.withitem) -> bool:
    call = item.context_expr
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in SESSION_OPENERS
    if isinstance(func, ast.Attribute):
        return func.attr in ("session_scope", "begin")
    return False


class Visitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.depth = 0
        self.awaits: list[str] = []
        self.nested: list[str] = []

    def _where(self, node: ast.AST) -> str:
        return f"{self.path.relative_to(APP_DIR)}:{node.lineno}"

    def visit_With(self, node: ast.With) -> None:
        self._with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._with(node)

    def _with(self, node) -> None:
        opens = any(_is_session_ctx(item) for item in node.items)
        if opens:
            if self.depth:
                self.nested.append(self._where(node))
            self.depth += 1
        for child in node.body:
            self.visit(child)
        if opens:
            self.depth -= 1

    def visit_Await(self, node: ast.Await) -> None:
        if self.depth:
            self.awaits.append(self._where(node))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Вложенная функция живёт своей жизнью: её вызовут когда угодно, и
        # считать её телом текущей транзакции неверно.
        outer, self.depth = self.depth, 0
        self.generic_visit(node)
        self.depth = outer

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]


def _scan() -> tuple[list[str], list[str]]:
    awaits: list[str] = []
    nested: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        visitor = Visitor(path)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        awaits.extend(visitor.awaits)
        nested.extend(visitor.nested)
    return awaits, nested


def test_scanner_actually_detects_the_pattern() -> None:
    """Самопроверка: без неё оба теста ниже могли бы проходить впустую.

    Разбор по AST легко сломать так, что он перестанет что-либо находить, — и
    зелёные тесты будут означать «сканер сломан», а не «код чистый».
    """
    sample = (
        "async def bad():\n"
        "    with session_scope() as s:\n"
        "        await client.get('x')\n"
        "        with session_scope() as s2:\n"
        "            pass\n"
    )
    visitor = Visitor(APP_DIR / "sample.py")
    visitor.visit(ast.parse(sample))
    assert visitor.awaits, "сканер не увидел await внутри транзакции"
    assert visitor.nested, "сканер не увидел вложенную транзакцию"


def test_no_await_inside_a_transaction() -> None:
    """Сетевой вызов под открытой транзакцией занимает базу на всё его время."""
    awaits, _ = _scan()
    assert not awaits, (
        "внутри session_scope() найден await: " + ", ".join(awaits) + ". "
        "Вынесите ожидание наружу — пока транзакция открыта, писать не может никто"
    )


def test_no_nested_transactions() -> None:
    """Вложенная транзакция ждёт внешнюю на том же потоке."""
    _, nested = _scan()
    assert not nested, (
        "вложенный session_scope(): " + ", ".join(nested) + ". "
        "Соберите данные заранее и откройте одну транзакцию"
    )
