"""Инкрементальный скан пересопоставляет только изменившееся.

⚠️ Заведено по замеру на живой библиотеке. Сопоставление стоит около 0,12 с на
папку, папок 591 — то есть каждый скан тратил на матчер около семидесяти секунд
при семи секундах на обход диска. Наблюдатель за файловой системой запускал скан
на любое изменение, а автоскачивание субтитров всё время что-то дописывало, и
проходы шли подряд: `21:58:45 → 22:00:43`, `22:00:44 → 22:02:42`, и так далее.

Пока это крутилось, страницы ждали: `POST /api/catalog/cards`, отправленный в
22:02:05, ответил в 22:02:27. Работа была не в базе и не на диске — чистый
питоновский счёт внутри того же процесса.

Отсюда правило: пересопоставляем ровно то, что разобрал обход, плюс детей —
подпапка сезона берёт название и `.nfo` у родителя. Сверочный проход по
расписанию (`full=True`) по-прежнему считает всё.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.library import LibraryFolder, TitleMapping
from app.services import library_service
from app.services.library_scan import scan_root
from app.services.library_service import (
    _known_states,
    _map_folders,
    _persist_root,
    _touched_folder_ids,
    build_index,
)

SERIES_ID = 940_101


def _seed() -> None:
    with session_scope() as session:
        if session.get(Series, SERIES_ID) is None:
            session.add(
                Series(
                    id=SERIES_ID,
                    title="Bocchi the Rock!",
                    titles={"romaji": "Bocchi the Rock!"},
                    all_titles=["Bocchi the Rock!"],
                    type="tv",
                    year=2022,
                )
            )


def _tree(base: Path) -> Path:
    root = base / "series"
    for name in ("Bocchi the Rock!", "Toradora!", "New Game!"):
        season = root / name / "Season 1"
        season.mkdir(parents=True)
        (season / f"{name} S01E01.mkv").write_bytes(b"x" * 10)
    return root


def _first_pass(root: Path) -> int:
    """Первый проход — как у чистой установки: известных слепков ещё нет."""
    _seed()
    return _persist_root(scan_root(str(root), "series"))


def _rescan(root: Path):
    """Повторный обход с оглядкой на прошлый — то, что делает инкремент."""
    return scan_root(str(root), "series", known=_known_states(str(root)))


def _folder_id(root_id: int, rel_path: str) -> int:
    with session_scope() as session:
        return session.execute(
            select(LibraryFolder.id).where(
                LibraryFolder.root_id == root_id, LibraryFolder.rel_path == rel_path
            )
        ).scalar_one()


class TestWhatCountsAsTouched:
    def test_first_pass_touches_everything(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        root_id = _first_pass(root)

        # Разбирали всё, значит и пересопоставить надо всё: шесть папок —
        # три тайтла и три сезона.
        assert len(_touched_folder_ids(root_id, scan_root(str(root), "series"))) == 6

    def test_nothing_changed_means_nothing_to_do(self, tmp_path: Path) -> None:
        """Главный случай: скан на пустом месте не должен стоить ничего."""
        root = _tree(tmp_path)
        root_id = _first_pass(root)

        assert _touched_folder_ids(root_id, _rescan(root)) == set()

    def test_a_new_file_touches_only_its_folder(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        root_id = _first_pass(root)

        (root / "Toradora!" / "Season 1" / "Toradora! S01E02.mkv").write_bytes(b"x" * 10)
        touched = _touched_folder_ids(root_id, _rescan(root))

        assert touched == {_folder_id(root_id, "Toradora!/Season 1")}

    def test_a_changed_title_folder_takes_its_seasons_along(self, tmp_path: Path) -> None:
        """Jellyfin переписал `tvshow.nfo` — сезоны наследуют его, и их тоже надо пересчитать."""
        root = _tree(tmp_path)
        root_id = _first_pass(root)

        (root / "New Game!" / "tvshow.nfo").write_text(
            "<tvshow><title>New Game!</title></tvshow>", encoding="utf-8"
        )
        touched = _touched_folder_ids(root_id, _rescan(root))

        assert touched == {
            _folder_id(root_id, "New Game!"),
            _folder_id(root_id, "New Game!/Season 1"),
        }


class TestScopedMappingCountsTheWork:
    def test_only_the_named_folders_reach_the_matcher(self, tmp_path: Path, monkeypatch) -> None:
        """Тот самый счёт, который занимал семьдесят секунд."""
        root = _tree(tmp_path)
        root_id = _first_pass(root)
        index = build_index()

        calls: list[str] = []
        original = library_service.Matcher.match

        def counting(self, ctx):  # type: ignore[no-untyped-def]
            calls.append(ctx.folder_name)
            return original(self, ctx)

        monkeypatch.setattr(library_service.Matcher, "match", counting)

        _map_folders(root_id, index, "series")
        assert len(calls) == 6, "без сужения считаются все папки"

        calls.clear()
        one = {_folder_id(root_id, "Toradora!/Season 1")}
        _map_folders(root_id, index, "series", only_folder_ids=one)

        assert len(calls) == 1

    def test_a_season_still_knows_its_parent(self, tmp_path: Path) -> None:
        """⚠️ Сузив выборку папок, легко потерять родителя — и смысл подпапки.

        Папка сезона берёт название у родителя. Если бы сужение выбрасывало
        родителя из выборки, `Season 1` осталась бы сама по себе и уехала бы
        не туда — либо не привязалась вовсе.
        """
        root = _tree(tmp_path)
        root_id = _first_pass(root)

        season = _folder_id(root_id, "Bocchi the Rock!/Season 1")
        _map_folders(root_id, build_index(), "series", only_folder_ids={season})

        with session_scope() as session:
            bound = session.execute(
                select(TitleMapping.series_id).where(TitleMapping.folder_id == season)
            ).scalar_one_or_none()

        assert bound == SERIES_ID

    def test_the_rest_keeps_its_bindings(self, tmp_path: Path) -> None:
        """Пересчёт одной папки не имеет права трогать соседей."""
        root = _tree(tmp_path)
        root_id = _first_pass(root)
        _map_folders(root_id, build_index(), "series")

        before = _bindings(root_id)
        assert before, "проверять нечего, если ничего не привязалось"

        one = {_folder_id(root_id, "Toradora!/Season 1")}
        _map_folders(root_id, build_index(), "series", only_folder_ids=one)

        assert _bindings(root_id) == before


def _bindings(root_id: int) -> dict[str, int]:
    with session_scope() as session:
        rows = session.execute(
            select(LibraryFolder.rel_path, TitleMapping.series_id)
            .join(TitleMapping, TitleMapping.folder_id == LibraryFolder.id)
            .where(LibraryFolder.root_id == root_id)
        ).all()
    return {rel: series_id for rel, series_id in rows}
