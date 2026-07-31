"""Повторное извлечение той же дорожки.

Кнопка «только звук» ничем не защищена от второго нажатия, а имя файла у одного
и того же перевода получается одинаковым — значит рядом лёг бы дубль. Проверка
стоит в трёх местах, и тесты следят за главным из них: отказом на постановке,
потому что только он даёт пользователю ответ сразу.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.db import session_scope
from app.models.downloads import DownloadTask, TaskStatus
from app.services.download_queue import existing_audio_path

TRANSLATION = 987654


@pytest.fixture
def finished_audio_task(tmp_path):
    """Завершённая задача на звук с реально существующим файлом."""
    target = tmp_path / "Тайтл - 01 [anime365_Автор].ru.m4a"
    target.write_bytes(b"audio")

    with session_scope() as session:
        task = DownloadTask(
            translation_id=TRANSLATION,
            series_id=1,
            kind="audio",
            status=TaskStatus.DONE,
            target_path=str(target),
            series_title="Тайтл",
            authors="Автор",
        )
        session.add(task)
        session.flush()
        task_id = task.id

    yield target, task_id

    with session_scope() as session:
        row = session.get(DownloadTask, task_id)
        if row is not None:
            session.delete(row)


class TestAlreadyExtracted:
    def test_finds_the_existing_track(self, finished_audio_task):
        target, _ = finished_audio_task
        assert existing_audio_path(TRANSLATION) == target

    def test_missing_file_does_not_count(self, finished_audio_task):
        # Запись о задаче переживает удаление файла руками. Считать такое
        # «уже скачано» значило бы не дать перекачать удалённое.
        target, _ = finished_audio_task
        target.unlink()
        assert existing_audio_path(TRANSLATION) is None

    def test_other_translation_is_not_confused(self, finished_audio_task):
        assert existing_audio_path(TRANSLATION + 1) is None

    def test_unfinished_task_does_not_count(self, finished_audio_task):
        # Пока задача не завершена, файла на месте ещё нет — блокировать
        # повторную постановку по ней нельзя.
        _, task_id = finished_audio_task
        with session_scope() as session:
            session.get(DownloadTask, task_id).status = TaskStatus.DOWNLOADING
        assert existing_audio_path(TRANSLATION) is None
