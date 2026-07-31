"""Определение дорожек через ffprobe.

Главное здесь не «правильно ли распознали» — это забота ffmpeg, — а «сколько
раз мы полезли на диск». Библиотека лежит на 5400 rpm, с которого одновременно
качают и стримят, поэтому каждое лишнее открытие файла отбирается у загрузок.
Тесты про отбор кандидатов: кого трогаем, а кого нет.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app.core.db import session_scope
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot
from app.services.stream_probe import StreamProbe


@pytest.fixture
def library(tmp_path):
    """Корень с четырьмя файлами в разных состояниях проверки."""
    with session_scope() as session:
        root = LibraryRoot(
            kind="series", path=str(tmp_path), is_writable=True, is_available=True
        )
        session.add(root)
        session.flush()
        folder = LibraryFolder(root_id=root.id, rel_path="Тайтл", name="Тайтл")
        session.add(folder)
        session.flush()

        files = {
            # Дорожки уже известны из .nfo — трогать нечего.
            "from_nfo.mkv": dict(
                streams={"audio_languages": ["jpn"], "subtitle_languages": []},
                mtime=100.0, probed_at=None, probed_mtime=None,
            ),
            # Не проверяли ни разу — кандидат.
            "fresh.mkv": dict(streams=None, mtime=100.0, probed_at=None, probed_mtime=None),
            # Проверяли, ничего не нашли, файл с тех пор не менялся — пропускаем.
            "checked_empty.mkv": dict(
                streams=None, mtime=100.0, probed_at=datetime.utcnow(), probed_mtime=100.0,
            ),
            # Проверяли, но файл заменили на месте — проверяем заново.
            "replaced.mkv": dict(
                streams=None, mtime=200.0, probed_at=datetime.utcnow(), probed_mtime=100.0,
            ),
        }
        ids = {}
        for name, fields in files.items():
            row = LibraryFile(folder_id=folder.id, filename=name, kind="video", **fields)
            session.add(row)
            session.flush()
            ids[name] = row.id
        folder_id, root_id = folder.id, root.id

    yield ids

    with session_scope() as session:
        for file_id in ids.values():
            row = session.get(LibraryFile, file_id)
            if row is not None:
                session.delete(row)
        session.delete(session.get(LibraryFolder, folder_id))
        session.delete(session.get(LibraryRoot, root_id))


class TestCandidateSelection:
    def test_nfo_data_wins_and_file_is_not_touched(self, library):
        names = {c["filename"] for c in StreamProbe._candidates()}
        # Данные из .nfo достались даром и надёжны — открывать файл незачем.
        assert "from_nfo.mkv" not in names

    def test_never_probed_file_is_a_candidate(self, library):
        names = {c["filename"] for c in StreamProbe._candidates()}
        assert "fresh.mkv" in names

    def test_probed_with_no_tracks_is_skipped(self, library):
        # Ровно тот случай, ради которого заведена отметка: без неё файл без
        # распознаваемых дорожек открывался бы на каждом скане заново.
        names = {c["filename"] for c in StreamProbe._candidates()}
        assert "checked_empty.mkv" not in names

    def test_replaced_file_is_probed_again(self, library):
        # mtime изменился — прежние дорожки уже неверны.
        names = {c["filename"] for c in StreamProbe._candidates()}
        assert "replaced.mkv" in names

    def test_force_revisits_everything_except_nfo(self, library):
        names = {c["filename"] for c in StreamProbe._candidates(force=True)}
        assert "checked_empty.mkv" in names
        # Даже жёсткий перескан не лезет туда, где есть .nfo: он про отметки
        # проверки, а не про недоверие к метаданным Jellyfin.
        assert "from_nfo.mkv" not in names


class TestDisabledByDefault:
    def test_does_nothing_while_the_toggle_is_off(self, library, monkeypatch):
        # asyncio.run, а не маркер pytest-asyncio: в этом окружении плагин не
        # подключён, и маркер молча превращает тест в пропущенный.
        from app.core.config import settings

        monkeypatch.setattr(settings, "get_bool", lambda key, *a: False)
        result = asyncio.run(StreamProbe().run())

        # Ни одного обращения к диску, пока тумблер выключен.
        assert result["probed"] == 0
        assert "выключено" in result["skipped"]
