"""Изменились правила разбора — содержимое читается заново.

Живой случай: правка «единственное видео в папке фильма — это первая серия»
уехала в образ, а `Fate+stay night - Heaven's Feel II. Lost Butterfly`
продолжал показывать «ни видео, ни субтитров». Файлы на месте, размер папки
считается, а связей серий нет.

Причина не в разборе, а в том, что разбора **не было**: инкрементальный скан
пропускает папку, у которой совпали mtime и число файлов. На диске ничего не
менялось — значит и перечитывать нечего. Пока правила неизменны, это верно;
стоит им поменяться, как в базе навсегда остаётся старый результат.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.core.db import session_scope
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot
from app.services.library_scan import PARSER_REVISION, scan_root
from app.services.library_service import _known_states, _persist_root

MOVIE = "Fate+stay night - Heaven's Feel II. Lost Butterfly"
VIDEO = f"{MOVIE} (2019) - [Bluray-1080p] [x265] [Opus 2.0] [JA].mkv"
SUBTITLE = f"{MOVIE} (2019) - [Bluray-1080p] [x265] [Opus 2.0] [JA].ja.srt"


def _build(root: Path) -> int:
    folder = root / MOVIE
    folder.mkdir(parents=True)
    (folder / VIDEO).write_bytes(b"x" * 10)
    (folder / SUBTITLE).write_text("dummy", encoding="utf-8")
    return _persist_root(scan_root(str(root), "movie"))


def _episodes(root_id: int) -> dict[str, float | None]:
    with session_scope() as session:
        rows = session.execute(
            select(LibraryFile.filename, LibraryFile.episode)
            .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
            .where(LibraryFolder.root_id == root_id)
        ).all()
    return {name: episode for name, episode in rows}


def _set_revision(root_id: int, value: int) -> None:
    with session_scope() as session:
        session.get(LibraryRoot, root_id).parser_revision = value


class TestParserRevision:
    def test_scan_stamps_the_revision(self, tmp_path: Path) -> None:
        root_id = _build(tmp_path / "movies-stamp")

        with session_scope() as session:
            assert session.get(LibraryRoot, root_id).parser_revision == PARSER_REVISION

    def test_same_revision_allows_incremental(self, tmp_path: Path) -> None:
        root = tmp_path / "movies-incr"
        _build(root)

        assert _known_states(str(root)) is not None, "лишний полный проход тоже плох"

    def test_changed_revision_forces_a_full_pass(self, tmp_path: Path) -> None:
        """Тот самый случай: на диске не менялось ничего, а перечитать надо."""
        root = tmp_path / "movies-stale"
        root_id = _build(root)
        _set_revision(root_id, PARSER_REVISION - 1)

        assert _known_states(str(root)) is None

    def test_stale_folder_is_reparsed(self, tmp_path: Path) -> None:
        """Проверка по существу: номера серий появляются после полного прохода."""
        root = tmp_path / "movies-reparse"
        root_id = _build(root)

        # Так выглядит база, разобранная старыми правилами: номеров нет.
        with session_scope() as session:
            for row in session.execute(
                select(LibraryFile)
                .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
                .where(LibraryFolder.root_id == root_id)
            ).scalars():
                row.episode = None
        _set_revision(root_id, PARSER_REVISION - 1)
        assert set(_episodes(root_id).values()) == {None}

        known = _known_states(str(root))
        _persist_root(scan_root(str(root), "movie", known=known))

        assert _episodes(root_id) == {VIDEO: 1.0, SUBTITLE: 1.0}

    def test_unknown_root_is_a_full_pass(self, tmp_path: Path) -> None:
        assert _known_states(str(tmp_path / "никогда-не-сканировали")) is None


class TestArtworkIsNotWeight:
    """Обложки и `.nfo` — файлы медиасервера, а не библиотеки.

    У папки без единой серии набегало по несколько мегабайт постеров и
    баннеров, и «занято 5 МБ» читалось как «что-то скачано».
    """

    def test_folder_with_only_artwork_weighs_nothing(self, tmp_path: Path) -> None:
        from app.services.library_scan import scan_root

        root = tmp_path / "artwork-only"
        folder = root / "Show"
        folder.mkdir(parents=True)
        for name in ("poster.jpg", "folder.jpg", "banner.jpg", "logo.png", "tvshow.nfo"):
            (folder / name).write_bytes(b"x" * 500_000)

        result = scan_root(str(root), "series")

        assert result.folders[0].size_bytes == 0

    def test_real_files_still_count(self, tmp_path: Path) -> None:
        from app.services.library_scan import scan_root

        root = tmp_path / "with-video"
        folder = root / "Show"
        folder.mkdir(parents=True)
        (folder / "poster.jpg").write_bytes(b"x" * 500_000)
        (folder / "Show S01E01 [G] WEBDL-1080p.mkv").write_bytes(b"x" * 1_000)
        (folder / "Show S01E01 [G] WEBDL-1080p.ru.ass").write_bytes(b"x" * 100)

        result = scan_root(str(root), "series")

        assert result.folders[0].size_bytes == 1_100

    def test_users_own_pictures_are_not_touched(self, tmp_path: Path) -> None:
        """Набор фиксированный: выкидывать все картинки подряд нельзя."""
        from app.services.library_scan import scan_root

        root = tmp_path / "own-picture"
        folder = root / "Show"
        folder.mkdir(parents=True)
        (folder / "моя картинка.jpg").write_bytes(b"x" * 700)

        result = scan_root(str(root), "series")

        assert result.folders[0].size_bytes == 700
