"""Курсор инкрементальной синхронизации каталога.

Ошибка, ради которой заведён этот файл: курсор выводился из `MAX(series.id)`.
Но в ту же таблицу пишут синхронизация онгоингов и онлайн-поиск, и оба приносят
тайтлы с произвольными id — в том числе самыми большими в каталоге.

Проверено на живой базе: на пустом каталоге синк онгоингов дал 306 тайтлов с
максимальным id 44219, после чего инкремент пошёл с `afterId=44219`. Тайтлы с
меньшими id — почти весь каталог — он не забрал бы уже никогда, а снаружи это
выглядело бы как успешно отработавший инкремент.
"""

from __future__ import annotations

from app.core.db import session_scope
from app.models.catalog import Series
from app.services.catalog_sync import _load_cursor, _save_cursor


class TestCursorIsIndependentOfCatalogContents:
    def test_no_cursor_at_first(self) -> None:
        """Без курсора инкремент обязан уступить место полному проходу."""
        with session_scope() as session:
            session.query(Series).filter(Series.id >= 900000).delete()
        _clear_cursor()
        assert _load_cursor() is None

    def test_titles_from_other_sources_do_not_move_it(self) -> None:
        """Тайтл с огромным id, попавший из поиска, курсор не двигает."""
        _clear_cursor()
        _save_cursor(1000)

        with session_scope() as session:
            session.merge(Series(id=999999, title="Пришёл из онлайн-поиска", type="tv"))

        assert _load_cursor() == 1000, (
            "курсор пошёл за содержимым таблицы — значит инкремент снова начнёт "
            "пропускать тайтлы с меньшими id"
        )

    def test_cursor_survives_and_updates(self) -> None:
        _clear_cursor()
        _save_cursor(100)
        assert _load_cursor() == 100
        _save_cursor(250)
        assert _load_cursor() == 250

    def test_only_one_row_is_kept(self) -> None:
        """История курсора не нужна, а инкремент идёт каждый час."""
        from sqlalchemy import func, select

        from app.models.catalog import ScanRun
        from app.services.catalog_sync import CURSOR_KIND

        _clear_cursor()
        for value in (1, 2, 3, 4, 5):
            _save_cursor(value)

        with session_scope() as session:
            rows = session.execute(
                select(func.count(ScanRun.id)).where(ScanRun.kind == CURSOR_KIND)
            ).scalar()
        assert rows == 1
        assert _load_cursor() == 5


def _clear_cursor() -> None:
    from app.models.catalog import ScanRun
    from app.services.catalog_sync import CURSOR_KIND

    with session_scope() as session:
        session.query(ScanRun).filter(ScanRun.kind == CURSOR_KIND).delete()
