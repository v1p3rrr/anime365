"""Инкрементальное сканирование библиотеки.

Самое опасное здесь — не «не сэкономили время», а «пропустили изменение» или
«стёрли разобранные файлы». Поэтому тесты про то, что именно попадает в
результат прохода, а не про скорость.
"""

from __future__ import annotations

from pathlib import Path

from app.services.filename_rules import get_rules
from app.services.library_scan import FolderState, scan_folder


def _make_title(root: Path, name: str, episodes: int = 2) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    for i in range(1, episodes + 1):
        (folder / f"{name} S01E{i:02d} - Serie [Group] WEBDL-1080p.mkv").write_bytes(b"x" * 10)
    return folder


def _states(scanned) -> dict[str, FolderState]:
    """Слепок так, как его собрал бы library_service из БД."""
    out: dict[str, FolderState] = {}

    def walk(folder) -> None:
        out[folder.rel_path] = FolderState(mtime=folder.mtime, file_count=len(folder.files))
        for child in folder.children:
            walk(child)

    walk(scanned)
    return out


class TestIncrementalScan:
    def test_first_pass_parses_everything(self, tmp_path: Path) -> None:
        folder = _make_title(tmp_path, "Frieren")
        result = scan_folder(folder, tmp_path, "series", get_rules())

        assert result.unchanged is False
        assert len(result.files) == 2

    def test_unchanged_folder_is_skipped(self, tmp_path: Path) -> None:
        folder = _make_title(tmp_path, "Frieren")
        rules = get_rules()
        first = scan_folder(folder, tmp_path, "series", rules)

        second = scan_folder(folder, tmp_path, "series", rules, known=_states(first))

        assert second.unchanged is True
        # Файлы не разбирались — их нет в результате. Именно поэтому запись в БД
        # обязана пропускать такие папки, а не переписывать их пустым списком.
        assert second.files == []

    def test_added_file_forces_rescan(self, tmp_path: Path) -> None:
        folder = _make_title(tmp_path, "Frieren")
        rules = get_rules()
        first = scan_folder(folder, tmp_path, "series", rules)
        known = _states(first)

        (folder / "Frieren S01E03 - Serie [Group] WEBDL-1080p.mkv").write_bytes(b"x" * 10)
        second = scan_folder(folder, tmp_path, "series", rules, known=known)

        assert second.unchanged is False
        assert len(second.files) == 3

    def test_removed_file_forces_rescan(self, tmp_path: Path) -> None:
        folder = _make_title(tmp_path, "Frieren", episodes=3)
        rules = get_rules()
        known = _states(scan_folder(folder, tmp_path, "series", rules))

        next(folder.glob("*E03*")).unlink()
        second = scan_folder(folder, tmp_path, "series", rules, known=known)

        assert second.unchanged is False
        assert len(second.files) == 2

    def test_change_inside_subfolder_is_noticed(self, tmp_path: Path) -> None:
        """Ключевой случай: mtime родителя не меняется от правок в подпапке.

        Если бы неизменившаяся папка тайтла обрывала обход, новая серия внутри
        `Season 01` осталась бы невидимой до сверочного скана.
        """
        title = tmp_path / "Yuru Camp"
        season = title / "Season 01"
        season.mkdir(parents=True)
        (season / "Yuru Camp S01E01 - A [G] WEBDL-1080p.mkv").write_bytes(b"x")

        rules = get_rules()
        known = _states(scan_folder(title, tmp_path, "series", rules))

        (season / "Yuru Camp S01E02 - B [G] WEBDL-1080p.mkv").write_bytes(b"x")
        second = scan_folder(title, tmp_path, "series", rules, known=known)

        assert second.unchanged is True, "сама папка тайтла действительно не менялась"
        child = second.children[0]
        assert child.unchanged is False
        assert len(child.files) == 2

    def test_unknown_folder_is_always_parsed(self, tmp_path: Path) -> None:
        folder = _make_title(tmp_path, "Новый тайтл")
        # Слепков нет вовсе — папка появилась после прошлого прохода.
        result = scan_folder(folder, tmp_path, "series", get_rules(), known={})

        assert result.unchanged is False
        assert len(result.files) == 2

    def test_full_pass_ignores_states(self, tmp_path: Path) -> None:
        folder = _make_title(tmp_path, "Frieren")
        rules = get_rules()
        _states(scan_folder(folder, tmp_path, "series", rules))

        # known=None — это и есть сверочный полный проход по расписанию.
        result = scan_folder(folder, tmp_path, "series", rules, known=None)

        assert result.unchanged is False
        assert len(result.files) == 2


class TestFileIdIsStableAcrossScans:
    """Идентификаторы файлов обязаны переживать сканирование.

    Здесь была тихая потеря данных: `_persist_folder` удалял строки файлов
    и вставлял их заново, а `episode_mapping.file_id` объявлен с ondelete=CASCADE.
    Каскад уносил все связи серий с файлами, включая ручные, — и проверка
    `is_manual` в `_map_episodes` работала уже по пустой таблице.
    Симптом: ручная привязка серии не переживала ни одного скана.
    """

    @staticmethod
    def _file_ids(root_id: int) -> dict[str, int]:
        from sqlalchemy import select

        from app.core.db import session_scope
        from app.models.library import LibraryFile, LibraryFolder

        with session_scope() as session:
            return {
                f.filename: f.id
                for f in session.execute(
                    select(LibraryFile)
                    .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
                    .where(LibraryFolder.root_id == root_id)
                ).scalars()
            }

    def test_rows_are_updated_not_recreated(self, tmp_path: Path) -> None:
        from app.core.db import Base, engine
        from app.services.library_scan import scan_root
        from app.services.library_service import _persist_root

        Base.metadata.create_all(engine)

        root = tmp_path / "series"
        _make_title(root, "Frieren", episodes=2)

        root_id = _persist_root(scan_root(str(root), "series"))
        before = self._file_ids(root_id)
        assert len(before) == 2

        # Повторный полный проход — так ходит сверочный скан по расписанию.
        _persist_root(scan_root(str(root), "series"))
        after = self._file_ids(root_id)

        assert before == after, "file_id изменился — каскад снесёт связи серий"

    def test_manual_episode_mapping_survives_rescan(self, tmp_path: Path) -> None:
        """То, ради чего всё и затевалось: ручная привязка переживает скан."""
        from sqlalchemy import delete as sa_delete
        from sqlalchemy import select

        from app.core.db import Base, engine, session_scope
        from app.models.catalog import Series
        from app.models.library import EpisodeMapping
        from app.services.library_scan import scan_root
        from app.services.library_service import _persist_root

        Base.metadata.create_all(engine)

        series_id = 999001
        with session_scope() as session:
            if session.get(Series, series_id) is None:
                session.add(Series(id=series_id, title="Тестовый тайтл", type="tv"))
            # БД общая на весь прогон: остатки прошлого запуска этого же теста
            # выглядели бы как «привязка выжила», хотя выжила чужая.
            session.execute(
                sa_delete(EpisodeMapping).where(EpisodeMapping.series_id == series_id)
            )

        root = tmp_path / "series-manual"
        _make_title(root, "Frieren", episodes=2)
        root_id = _persist_root(scan_root(str(root), "series"))
        file_id = next(iter(self._file_ids(root_id).values()))

        with session_scope() as session:
            session.add(
                EpisodeMapping(
                    series_id=series_id, episode=1, file_id=file_id, role="video", is_manual=True
                )
            )

        _persist_root(scan_root(str(root), "series"))

        with session_scope() as session:
            survived = session.execute(
                select(EpisodeMapping).where(
                    EpisodeMapping.series_id == series_id, EpisodeMapping.is_manual.is_(True)
                )
            ).scalars().all()

        assert survived, "ручная привязка снесена каскадом при пересканировании"
        assert survived[0].file_id == file_id

    def test_deleted_file_row_disappears(self, tmp_path: Path) -> None:
        from app.core.db import Base, engine
        from app.services.library_scan import scan_root
        from app.services.library_service import _persist_root

        Base.metadata.create_all(engine)

        root = tmp_path / "series-del"
        folder = _make_title(root, "Frieren", episodes=3)
        root_id = _persist_root(scan_root(str(root), "series"))

        next(folder.glob("*E03*")).unlink()
        _persist_root(scan_root(str(root), "series"))

        names = list(self._file_ids(root_id))
        # Переиспользование строк не должно превращаться в «ничего не удаляем».
        assert not any("E03" in n for n in names)
        assert len(names) == 2
