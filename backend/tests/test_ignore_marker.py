"""Папка с файлом `.ignore` пропускается — так же, как у Jellyfin.

Соглашение чужое, но человек уже разметил им свою библиотеку, и другого способа
сказать нам то же самое у него не было. В библиотеке пользователя таких меток
**четырнадцать**, и лежат они ровно там, где у нас были неверные привязки:

* `Toradora!/Specials/BD Scans` — садилась на `MM! Specials` с похожестью 76;
* `Honzuki no Gekokujou/… S1/S2/S3 x265 10bit` — релизные дубли, занимавшие те
  же тайтлы, что настоящие папки сезонов;
* `Girls Band Cry/subtitles`, `ScreenShot`, `EXTRA`, `subs2srs output` —
  служебные папки, наследовавшие тайтл родителя;
* `New Game!/Specials/oped`, `Toradora!/Season 1/oped`, `Fate+Zero/NC`,
  `Watashi ga Koibito…/Creditless` — заставки.

⚠️ Пропустить мало — надо снять прежнюю привязку. Урок D-141: «не обработать» и
«очистить» здесь разные действия, и первое само по себе ничего не убирает.
Здесь это выходит само: папки нет в результате прохода, значит она уходит как
исчезнувшая, вместе с файлами и привязками.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.library import LibraryFile, LibraryFolder, TitleMapping
from app.services.library_scan import is_ignored, scan_root
from app.services.library_service import _persist_root

#: Тот самый тайтл, на который садилась `Toradora!/Specials/BD Scans`.
SERIES_ID = 930_501


def _tree(base: Path) -> Path:
    """Кусок библиотеки, повторяющий раскладку с сервера."""
    root = base / "series"
    (root / "Toradora!" / "Season 1").mkdir(parents=True)
    (root / "Toradora!" / "Season 1" / "Toradora! S01E01.mkv").write_bytes(b"x" * 10)

    scans = root / "Toradora!" / "Specials" / "BD Scans"
    scans.mkdir(parents=True)
    (scans / "scan01.mkv").write_bytes(b"x" * 10)
    (scans / ".ignore").write_text("", encoding="utf-8")

    oped = root / "Toradora!" / "Season 1" / "oped"
    oped.mkdir()
    (oped / "Toradora! S01E01 NCOP.mkv").write_bytes(b"x" * 10)
    (oped / ".ignore").write_text("", encoding="utf-8")
    return root


def _folders(root_id: int) -> set[str]:
    with session_scope() as session:
        return set(
            session.execute(
                select(LibraryFolder.rel_path).where(LibraryFolder.root_id == root_id)
            ).scalars()
        )


class TestMarkerItself:
    def test_empty_file_is_enough(self, tmp_path: Path) -> None:
        folder = tmp_path / "folder"
        folder.mkdir()
        assert not is_ignored(folder)

        (folder / ".ignore").write_text("", encoding="utf-8")
        assert is_ignored(folder)

    def test_a_directory_named_ignore_does_not_count(self, tmp_path: Path) -> None:
        """Метка — файл. Папка с таким именем ничего не значит."""
        folder = tmp_path / "folder"
        (folder / ".ignore").mkdir(parents=True)
        assert not is_ignored(folder)


class TestScanSkipsMarkedFolders:
    def test_marked_subfolders_are_absent(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)

        found = {f.rel_path for f in scan_root(str(root), "series").folders}
        children = {
            child.rel_path
            for folder in scan_root(str(root), "series").folders
            for child in folder.children
        }

        assert "Toradora!" in found
        assert "Toradora!/Season 1" in children
        assert "Toradora!/Specials" in children
        assert not any("BD Scans" in path for path in children)
        assert not any("oped" in path for path in children)

    def test_a_marked_title_folder_is_skipped_whole(self, tmp_path: Path) -> None:
        root = tmp_path / "series"
        (root / "Honzuki S1 x265 10bit").mkdir(parents=True)
        (root / "Honzuki S1 x265 10bit" / ".ignore").write_text("", encoding="utf-8")
        (root / "Honzuki S1 x265 10bit" / "Honzuki S01E01.mkv").write_bytes(b"x" * 10)
        (root / "Honzuki no Gekokujou").mkdir()

        found = {f.rel_path for f in scan_root(str(root), "series").folders}

        assert found == {"Honzuki no Gekokujou"}

    def test_files_inside_are_not_counted(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        result = scan_root(str(root), "series")

        def files(folder) -> int:
            return len(folder.files) + sum(files(child) for child in folder.children)

        assert sum(files(f) for f in result.folders) == 1, "видео из BD Scans и oped не в счёт"


class TestOldBindingIsCleared:
    """Пропустить мало — прежняя привязка обязана исчезнуть (D-141)."""

    def test_folder_and_its_mapping_disappear(self, tmp_path: Path) -> None:
        root = tmp_path / "series"
        scans = root / "Toradora!" / "Specials" / "BD Scans"
        scans.mkdir(parents=True)
        (scans / "scan01.mkv").write_bytes(b"x" * 10)

        # Первый скан — метки ещё нет, папка попадает в базу и получает привязку.
        root_id = _persist_root(scan_root(str(root), "series"))
        with session_scope() as session:
            # У привязки внешний ключ на тайтл — без строки вставка не пройдёт.
            if session.get(Series, SERIES_ID) is None:
                session.add(Series(id=SERIES_ID, title="MM! Specials", type="special"))
            folder_id = session.execute(
                select(LibraryFolder.id).where(
                    LibraryFolder.root_id == root_id,
                    LibraryFolder.rel_path == "Toradora!/Specials/BD Scans",
                )
            ).scalar_one()
            session.add(
                TitleMapping(
                    series_id=SERIES_ID,
                    folder_id=folder_id,
                    source="fuzzy",
                    confidence=0.76,
                )
            )

        assert "Toradora!/Specials/BD Scans" in _folders(root_id)

        # Человек поставил метку — и повторный скан обязан убрать и папку, и связь.
        (scans / ".ignore").write_text("", encoding="utf-8")
        _persist_root(scan_root(str(root), "series"))

        assert "Toradora!/Specials/BD Scans" not in _folders(root_id)
        with session_scope() as session:
            left = session.execute(
                select(TitleMapping.id).where(TitleMapping.folder_id == folder_id)
            ).scalars().all()
            files = session.execute(
                select(LibraryFile.id).where(LibraryFile.folder_id == folder_id)
            ).scalars().all()

        assert left == [], "привязка осталась висеть на несуществующей папке"
        assert files == []
