"""Сверка нашей библиотеки с тем, что распознал Jellyfin.

Главный риск здесь не «не нашли лишнего», а обратный: принять неудачный запрос
за пустой ответ и объявить нераспознанной всю библиотеку. Отчёт, который врёт в
эту сторону, хуже отсутствующего — по нему пойдут переименовывать файлы.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.core.db import session_scope
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot
from app.services import jellyfin_audit


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def library(tmp_path):
    """Три видеофайла в двух папках — и больше ничего.

    Сверка читает библиотеку целиком, поэтому фикстура обязана владеть всем её
    содержимым: чужие строки, оставшиеся от других тестов, попали бы в отчёт и
    сделали бы утверждения о числах бессмысленными.
    """
    with session_scope() as session:
        session.query(LibraryFile).delete()
        session.query(LibraryFolder).delete()
        session.query(LibraryRoot).delete()

    with session_scope() as session:
        root = LibraryRoot(
            kind="series", path=str(tmp_path), is_writable=True, is_available=True
        )
        session.add(root)
        session.flush()
        folder_a = LibraryFolder(root_id=root.id, rel_path="Show A", name="Show A")
        folder_b = LibraryFolder(root_id=root.id, rel_path="Show B", name="Show B")
        session.add_all([folder_a, folder_b])
        session.flush()
        for folder, names in ((folder_a, ["s01e01.mkv", "s01e02.mkv"]), (folder_b, ["s01e01.mkv"])):
            for name in names:
                session.add(
                    LibraryFile(
                        folder_id=folder.id, filename=name, kind="video",
                        size_bytes=100, mtime=1.0,
                    )
                )
        ids = {"root": root.id, "folders": [folder_a.id, folder_b.id]}

    yield ids

    with session_scope() as session:
        session.query(LibraryFile).delete()
        session.query(LibraryFolder).delete()
        session.query(LibraryRoot).delete()


def _configure(monkeypatch, known):
    """Подменяет ответ Jellyfin, оставляя всю логику сверки настоящей."""
    settings.set_db_override("enable_jellyfin", "true")
    settings.set_db_override("jellyfin_url", "http://jellyfin:8096")
    settings.set_db_override("jellyfin_api_key", "k")

    async def fake(_self=None):
        return known

    monkeypatch.setattr(jellyfin_audit.jellyfin, "known_file_paths", fake)


@pytest.fixture(autouse=True)
def _clean():
    yield
    for key in ("enable_jellyfin", "jellyfin_url", "jellyfin_api_key"):
        settings.set_db_override(key, None)


class TestAudit:
    def test_everything_recognised(self, library, monkeypatch):
        _configure(monkeypatch, {
            "/media/anime/show a/s01e01.mkv",
            "/media/anime/show a/s01e02.mkv",
            "/media/anime/show b/s01e01.mkv",
        })
        result = run(jellyfin_audit.audit())
        assert result["available"] is True
        assert result["missing_count"] == 0
        assert result["our_files"] == 3

    def test_missing_file_is_reported(self, library, monkeypatch):
        _configure(monkeypatch, {
            "/media/anime/show a/s01e01.mkv",
            "/media/anime/show b/s01e01.mkv",
        })
        result = run(jellyfin_audit.audit())
        assert result["missing_count"] == 1
        assert result["missing"][0]["filename"] == "s01e02.mkv"
        assert result["missing"][0]["folder"] == "Show A"

    def test_grouping_shows_the_pattern(self, library, monkeypatch):
        # Нераспознанное почти всегда идёт группами по одной причине, и папка —
        # то, по чему причина видна.
        _configure(monkeypatch, {"/media/anime/show b/s01e01.mkv"})
        result = run(jellyfin_audit.audit())
        assert result["by_folder"][0] == {"folder": "Show A", "count": 2}

    def test_unreachable_is_not_the_same_as_empty(self, library, monkeypatch):
        # ⚠️ Главный тест файла. На неудачном запросе отчёт обязан сказать
        # «не смог», а не «не распознано всё».
        _configure(monkeypatch, None)
        result = run(jellyfin_audit.audit())
        assert result["available"] is False
        assert result["reason"] == "unreachable"
        assert "missing_count" not in result

    def test_jellyfin_knows_nothing_is_a_real_answer(self, library, monkeypatch):
        # А вот пустое множество — это настоящий ответ: библиотека у Jellyfin
        # пуста, и все наши файлы действительно не распознаны.
        _configure(monkeypatch, set())
        result = run(jellyfin_audit.audit())
        assert result["available"] is True
        assert result["missing_count"] == 3

    def test_component_boundary_is_respected(self, library, monkeypatch):
        # «how a/s01e01.mkv» не должно засчитаться за «Show A/s01e01.mkv».
        _configure(monkeypatch, {
            "/media/anime/somehow a/s01e01.mkv",
            "/media/anime/show a/s01e02.mkv",
            "/media/anime/show b/s01e01.mkv",
        })
        result = run(jellyfin_audit.audit())
        assert result["missing_count"] == 1
        assert result["missing"][0]["folder"] == "Show A"
        assert result["missing"][0]["filename"] == "s01e01.mkv"

    def test_case_difference_does_not_produce_false_alarms(self, library, monkeypatch):
        _configure(monkeypatch, {
            "/media/anime/SHOW A/S01E01.MKV",
            "/media/anime/Show A/S01E02.mkv",
            "/media/anime/show b/s01e01.mkv",
        })
        assert run(jellyfin_audit.audit())["missing_count"] == 0


class TestUnavailable:
    def test_toggle_off(self, library):
        settings.set_db_override("enable_jellyfin", "false")
        result = run(jellyfin_audit.audit())
        assert result == {"available": False, "reason": "disabled"}

    def test_toggle_on_but_not_configured(self, library):
        settings.set_db_override("enable_jellyfin", "true")
        result = run(jellyfin_audit.audit())
        assert result["reason"] == "not_configured"

    def test_our_library_is_empty(self, monkeypatch):
        # Без своих файлов сверять не с чем, и «0 нераспознанных» здесь было бы
        # ложной хорошей новостью.
        _configure(monkeypatch, {"/media/anime/whatever.mkv"})
        monkeypatch.setattr(jellyfin_audit, "_our_video_files", lambda: [])
        result = run(jellyfin_audit.audit())
        assert result == {"available": False, "reason": "library_empty"}
