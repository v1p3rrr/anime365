"""Язык субтитров, указанный человеком.

Автоматика определяет его двумя способами — точно по `t{id}` и по письменности
([D-101](../../claude/09-decisions.md)), — но остаётся третий случай: файл без
суффикса, со смешанным текстом или вовсе на четвёртом языке.

⚠️ `other` — **не** синоним «неизвестно». `sub_unknown` значит «не смогли
определить» и зовёт разобраться; `other` значит «определили, это не ru/en/ja»:
файл лежит как есть и в покрытие по языкам не идёт.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, LibraryRoot

SERIES_ID = 996001
ROOT = "/test/manual-lang"
FOLDER = "Kagerou Days"
SUBTITLE = "Kagerou Days S01E01 [Group] WEBDL-1080p.ass"


def _seed() -> tuple[int, int]:
    """Папка с одним файлом субтитров. Возвращает id файла и id папки."""
    with session_scope() as session:
        if session.get(Series, SERIES_ID) is None:
            session.add(Series(id=SERIES_ID, title="Kagerou Days", type="tv"))

        root = session.execute(
            select(LibraryRoot).where(LibraryRoot.path == ROOT)
        ).scalar_one_or_none()
        if root is None:
            root = LibraryRoot(path=ROOT, kind="series")
            session.add(root)
            session.flush()
        session.execute(delete(LibraryFolder).where(LibraryFolder.root_id == root.id))
        session.execute(delete(EpisodeMapping).where(EpisodeMapping.series_id == SERIES_ID))
        session.flush()

        folder = LibraryFolder(root_id=root.id, rel_path=FOLDER, name=FOLDER, video_count=0)
        session.add(folder)
        session.flush()
        subtitle = LibraryFile(
            folder_id=folder.id,
            filename=SUBTITLE,
            kind="subtitle",
            lang=None,
            size_bytes=64,
            mtime=1.0,
            episode=1,
        )
        session.add(subtitle)
        session.flush()
        session.add(
            EpisodeMapping(
                series_id=SERIES_ID, episode=1, file_id=subtitle.id, role="sub_unknown"
            )
        )
        return subtitle.id, folder.id


def _set(client, file_id: int, lang: str | None) -> Any:
    return client.post(f"/api/library/files/{file_id}/language", json={"lang": lang})


def _row(file_id: int) -> tuple[str | None, bool, str]:
    with session_scope() as session:
        file = session.get(LibraryFile, file_id)
        role = session.execute(
            select(EpisodeMapping.role).where(EpisodeMapping.file_id == file_id)
        ).scalar_one()
        return file.lang, file.lang_is_manual, role


class TestManualLanguage:
    def test_setting_a_language_updates_the_role(self, client) -> None:
        file_id, _folder_id = _seed()

        response = _set(client, file_id, "ru")

        assert response.status_code == 200, response.text
        assert _row(file_id) == ("ru", True, "sub_ru")

    def test_other_is_not_unknown(self, client) -> None:
        """Главное отличие: «прочий» — это ответ, а не отсутствие ответа."""
        file_id, _folder_id = _seed()

        _set(client, file_id, "other")

        lang, manual, role = _row(file_id)
        assert (lang, manual) == ("other", True)
        assert role == "sub_other"
        assert role != "sub_unknown"

    def test_clearing_returns_the_file_to_automation(self, client) -> None:
        file_id, _folder_id = _seed()
        _set(client, file_id, "ja")

        _set(client, file_id, None)

        _lang, manual, _role = _row(file_id)
        assert manual is False

    def test_unknown_language_is_refused(self, client) -> None:
        """Код короткий, но не наш: сюда доходит именно наша проверка."""
        file_id, _folder_id = _seed()

        response = _set(client, file_id, "de")

        assert response.status_code == 400
        assert _row(file_id)[1] is False, "отказ не должен ничего менять"

    def test_overly_long_value_is_rejected_by_validation(self, client) -> None:
        """Длинное значение отсекается раньше — на разборе тела запроса."""
        file_id, _folder_id = _seed()

        assert _set(client, file_id, "клингонский").status_code == 422

    def test_video_has_no_language_to_set(self, client) -> None:
        """Язык — свойство субтитров. У видео дорожек несколько."""
        # ⚠️ Папку берём свою. Файл с таким именем есть и в других тестах —
        # база общая, и поиск по одному имени нашёл бы чужой.
        _file_id, folder_id = _seed()
        with session_scope() as session:
            video = LibraryFile(
                folder_id=folder_id,
                filename="Kagerou Days S01E01 [Group] WEBDL-1080p.mkv",
                kind="video",
                size_bytes=10,
                mtime=1.0,
                episode=1,
            )
            session.add(video)
            session.flush()
            video_id = video.id

        assert _set(client, video_id, "ru").status_code == 400

    def test_missing_file_is_404(self, client) -> None:
        assert _set(client, 99999999, "ru").status_code == 404


class TestScanDoesNotOverwrite:
    def test_manual_language_survives_a_rescan(self, tmp_path) -> None:
        """То, ради чего заведена колонка.

        Автоматика ошибается на файлах без суффикса, и молча переписывать
        решение человека — худшее, что она может сделать.
        """
        from app.services.library_scan import scan_root
        from app.services.library_service import _persist_root

        root = tmp_path / "series"
        folder = root / "Kagerou Days"
        folder.mkdir(parents=True)
        (folder / "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv").write_bytes(b"x" * 10)
        # Содержимое русское — автоматика определила бы `ru`.
        (folder / "Kagerou Days S01E01 [Group] WEBDL-1080p.ass").write_text(
            "[Script Info]\n" + "Dialogue: Привет, как дела сегодня? " * 40, encoding="utf-8"
        )

        root_id = _persist_root(scan_root(str(root), "series"))
        with session_scope() as session:
            row = session.execute(
                select(LibraryFile)
                .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
                .where(LibraryFolder.root_id == root_id, LibraryFile.kind == "subtitle")
            ).scalar_one()
            assert row.lang == "ru", "автоматика определила язык по письменности"
            # Человек говорит: это не русский, а посторонний язык.
            row.lang = "other"
            row.lang_is_manual = True
            file_id = row.id

        _persist_root(scan_root(str(root), "series"))

        with session_scope() as session:
            row = session.get(LibraryFile, file_id)
            assert (row.lang, row.lang_is_manual) == ("other", True), "скан перетёр человека"
