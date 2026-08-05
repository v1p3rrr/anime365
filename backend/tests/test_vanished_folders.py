"""Папки, исчезнувшие с диска, должны исчезать и из базы.

Файлы удалялись, папки — никогда. Живой случай: `Girls Band Cry/Movie 1 …` и
`Girls Band Cry/Movie 2 …` переехали из корня сериалов в корень фильмов, на
диске их там больше нет, а в базе они остались вместе с файлами и привязками.
Те же файлы считались дважды, а привязка вела на несуществующий путь.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select

from app.core.db import Base, engine, session_scope
from app.models.library import LibraryFile, LibraryFolder
from app.services.library_scan import scan_root
from app.services.library_service import _persist_root


def _make_season(root: Path, title: str, season: str, episodes: int = 2) -> Path:
    folder = root / title / season
    folder.mkdir(parents=True)
    for i in range(1, episodes + 1):
        (folder / f"{title} S01E{i:02d} [Group] WEBDL-1080p.mkv").write_bytes(b"x" * 10)
    return folder


def _paths(root_id: int) -> set[str]:
    with session_scope() as session:
        return {
            rel
            for (rel,) in session.execute(
                select(LibraryFolder.rel_path).where(LibraryFolder.root_id == root_id)
            ).all()
        }


def _file_count(root_id: int) -> int:
    with session_scope() as session:
        return len(
            session.execute(
                select(LibraryFile.id)
                .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
                .where(LibraryFolder.root_id == root_id)
            ).all()
        )


class TestVanishedFolders:
    def test_moved_away_folder_is_forgotten(self, tmp_path: Path) -> None:
        root = tmp_path / "series"
        _make_season(root, "Girls Band Cry", "Season 1")
        _make_season(root, "Girls Band Cry", "Movie 1 - Youth Rhapsody", episodes=1)

        root_id = _persist_root(scan_root(str(root), "series"))
        assert "Girls Band Cry/Movie 1 - Youth Rhapsody" in _paths(root_id)
        assert _file_count(root_id) == 3

        # Папку унесли в корень фильмов.
        shutil.rmtree(root / "Girls Band Cry" / "Movie 1 - Youth Rhapsody")
        _persist_root(scan_root(str(root), "series"))

        paths = _paths(root_id)
        assert "Girls Band Cry/Movie 1 - Youth Rhapsody" not in paths
        assert "Girls Band Cry/Season 1" in paths
        assert _file_count(root_id) == 2, "файлы исчезнувшей папки продолжают считаться"

    def test_whole_title_can_disappear(self, tmp_path: Path) -> None:
        root = tmp_path / "series-gone"
        _make_season(root, "Evangelion", "Season 1")
        _make_season(root, "Neon Genesis Evangelion", "Season 1")

        root_id = _persist_root(scan_root(str(root), "series"))
        assert len(_paths(root_id)) == 4

        shutil.rmtree(root / "Evangelion")
        _persist_root(scan_root(str(root), "series"))

        paths = _paths(root_id)
        assert not any(p.startswith("Evangelion") for p in paths)
        assert "Neon Genesis Evangelion/Season 1" in paths

    def test_unchanged_library_keeps_everything(self, tmp_path: Path) -> None:
        """Страховка от обратной беды: повторный проход не должен ничего сносить."""
        root = tmp_path / "series-stable"
        _make_season(root, "Frieren", "Season 1")

        root_id = _persist_root(scan_root(str(root), "series"))
        before = _paths(root_id)

        _persist_root(scan_root(str(root), "series"))

        assert _paths(root_id) == before
        assert _file_count(root_id) == 2


class TestUnreadableFolderIsKept:
    def test_subtree_that_failed_to_read_survives(self, tmp_path: Path) -> None:
        """Пустой список из-за ошибки чтения — это «неизвестно», а не «пусто».

        Иначе отвалившееся на секунду монтирование стёрло бы половину базы
        вместе с ручными привязками.
        """
        Base.metadata.create_all(engine)

        root = tmp_path / "series-unreadable"
        _make_season(root, "Frieren", "Season 1")
        root_id = _persist_root(scan_root(str(root), "series"))
        assert "Frieren/Season 1" in _paths(root_id)

        result = scan_root(str(root), "series")
        # Так выглядит папка, которую не удалось прочитать: детей нет, флаг стоит.
        title = result.folders[0]
        title.children = []
        title.read_failed = True
        _persist_root(result)

        assert "Frieren/Season 1" in _paths(root_id)
