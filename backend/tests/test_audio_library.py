"""Фонотека: список дорожек и отдача файла потоком.

Два места, где ошибиться дорого:

* **Range.** Без него `<audio>` не даёт перемотку вовсе, и ползунок остаётся
  декоративным. Ошибка тихая: звук играет, а мотать нельзя.
* **Путь к файлу.** Он берётся из записи задачи по идентификатору. Приняв путь
  от клиента, мы отдали бы наружу чтение любого файла на сервере.
"""

from __future__ import annotations

import pytest

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.downloads import DownloadTask, TaskStatus
from app.services.audio_library import library, track_path, tracks_for_series

SERIES = 777001
CONTENT = b"0123456789" * 20  # 200 байт — удобно считать границы


@pytest.fixture
def tracks(tmp_path):
    """Две дорожки на диске и одна запись без файла."""
    with session_scope() as session:
        session.query(DownloadTask).delete()
        if session.get(Series, SERIES) is None:
            session.add(Series(id=SERIES, title="Тайтл с музыкой", type="tv"))

    first = tmp_path / "S01E01.m4a"
    second = tmp_path / "S01E02.m4a"
    exotic = tmp_path / "S01E03.mka"
    for path in (first, second, exotic):
        path.write_bytes(CONTENT)

    ids = {}
    with session_scope() as session:
        for episode, path in ((1, first), (2, second), (3, exotic)):
            task = DownloadTask(
                translation_id=1000 + episode,
                series_id=SERIES,
                episode=float(episode),
                kind="audio",
                status=TaskStatus.DONE,
                series_title="Тайтл с музыкой",
                authors="Студия",
                lang="ru",
                target_path=str(path),
            )
            session.add(task)
            session.flush()
            ids[episode] = task.id

        # Запись есть, файла нет: так выглядит удаление файла руками.
        ghost = DownloadTask(
            translation_id=1099,
            series_id=SERIES,
            episode=9.0,
            kind="audio",
            status=TaskStatus.DONE,
            series_title="Тайтл с музыкой",
            target_path=str(tmp_path / "которого-нет.m4a"),
        )
        session.add(ghost)
        session.flush()
        ids["ghost"] = ghost.id

    yield ids

    with session_scope() as session:
        session.query(DownloadTask).delete()


class TestListing:
    def test_only_existing_files_are_listed(self, tracks):
        # ⚠️ Запись переживает удаление файла. Без проверки на диске список
        # предлагал бы слушать то, чего нет.
        episodes = [item["episode"] for item in tracks_for_series(SERIES)]
        assert episodes == [1.0, 2.0, 3.0]
        assert 9.0 not in episodes

    def test_exotic_container_is_listed_but_not_playable(self, tracks):
        # `.mka` браузер не откроет — показать дорожку надо, а кнопку
        # воспроизведения предлагать нечестно.
        by_episode = {item["episode"]: item for item in tracks_for_series(SERIES)}
        assert by_episode[1.0]["playable"] is True
        assert by_episode[3.0]["playable"] is False

    def test_size_and_author_come_from_the_task(self, tracks):
        first = tracks_for_series(SERIES)[0]
        assert first["size_bytes"] == len(CONTENT)
        assert first["authors"] == "Студия"
        assert first["lang"] == "ru"

    def test_library_groups_by_title(self, tracks):
        rows = library()
        assert len(rows) == 1
        assert rows[0]["series_id"] == SERIES
        assert rows[0]["track_count"] == 3
        assert rows[0]["size_bytes"] == len(CONTENT) * 3

    def test_other_kinds_are_not_audio(self, tracks):
        with session_scope() as session:
            session.add(
                DownloadTask(
                    translation_id=2000,
                    series_id=SERIES,
                    episode=5.0,
                    kind="video",
                    status=TaskStatus.DONE,
                    target_path=__file__,  # существующий файл
                )
            )
        assert all(item["episode"] != 5.0 for item in tracks_for_series(SERIES))


class TestPathSafety:
    def test_path_comes_from_the_task(self, tracks):
        assert track_path(tracks[1]) is not None

    def test_missing_file_gives_nothing(self, tracks):
        assert track_path(tracks["ghost"]) is None

    def test_unknown_task_gives_nothing(self):
        assert track_path(10_000_000) is None

    def test_video_task_is_not_served_as_audio(self, tracks):
        # Иначе через этот эндпоинт утекало бы видео в обход проверок.
        with session_scope() as session:
            task = DownloadTask(
                translation_id=3000,
                series_id=SERIES,
                kind="video",
                status=TaskStatus.DONE,
                target_path=__file__,
            )
            session.add(task)
            session.flush()
            task_id = task.id
        assert track_path(task_id) is None

    def test_unfinished_task_is_not_served(self, tracks):
        with session_scope() as session:
            task = DownloadTask(
                translation_id=3001,
                series_id=SERIES,
                kind="audio",
                status=TaskStatus.DOWNLOADING,
                target_path=__file__,
            )
            session.add(task)
            session.flush()
            task_id = task.id
        assert track_path(task_id) is None


class TestRange:
    """Без Range перемотки нет. Ошибка тихая, поэтому проверяем границы."""

    def test_whole_file_without_range(self, tracks, client):
        response = client.get(f"/api/stream/audio/{tracks[1]}")
        assert response.status_code == 200
        assert response.content == CONTENT
        assert response.headers["accept-ranges"] == "bytes"

    def test_partial_range(self, tracks, client):
        response = client.get(
            f"/api/stream/audio/{tracks[1]}", headers={"Range": "bytes=10-19"}
        )
        assert response.status_code == 206
        assert response.content == CONTENT[10:20]
        assert response.headers["content-range"] == f"bytes 10-19/{len(CONTENT)}"

    def test_open_ended_range(self, tracks, client):
        response = client.get(
            f"/api/stream/audio/{tracks[1]}", headers={"Range": "bytes=190-"}
        )
        assert response.status_code == 206
        assert response.content == CONTENT[190:]

    def test_suffix_range(self, tracks, client):
        # Форма «последние N байт» — ей пользуются плееры, читающие хвост
        # с метаданными перед началом воспроизведения.
        response = client.get(
            f"/api/stream/audio/{tracks[1]}", headers={"Range": "bytes=-20"}
        )
        assert response.status_code == 206
        assert response.content == CONTENT[-20:]

    def test_range_beyond_end_is_416(self, tracks, client):
        response = client.get(
            f"/api/stream/audio/{tracks[1]}", headers={"Range": "bytes=500-600"}
        )
        assert response.status_code == 416

    def test_malformed_range_falls_back_to_whole_file(self, tracks, client):
        # Кривой заголовок — не повод отказывать в воспроизведении.
        # Значение ASCII намеренно: заголовки иначе не отправляются в принципе,
        # и тест проверял бы httpx, а не наш разбор.
        response = client.get(
            f"/api/stream/audio/{tracks[1]}", headers={"Range": "bytes=abc-def"}
        )
        assert response.status_code == 200
        assert response.content == CONTENT

    def test_missing_file_is_404(self, tracks, client):
        assert client.get(f"/api/stream/audio/{tracks['ghost']}").status_code == 404
