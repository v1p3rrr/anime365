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


class TestJapaneseTrackIsSharedByEpisode:
    """Японская дорожка у серии одна, сколько бы переводов её ни несли.

    Перевод с субтитрами — это то же видео с той же японской дорожкой, что и
    равка. Извлекать её отдельно с каждого значило бы разложить рядом несколько
    одинаковых файлов. У озвучек наоборот: RU и EN у разных студий разные, и
    вторая озвучка той же серии — другая дорожка, а не дубль.
    """

    SERIES = 770001

    def _done_audio(self, tmp_path, translation_id: int, lang: str, episode: float):
        target = tmp_path / f"Тайтл - {episode:g} [{lang}].m4a"
        target.write_bytes(b"audio")
        with session_scope() as session:
            session.add(
                DownloadTask(
                    translation_id=translation_id,
                    series_id=self.SERIES,
                    episode=episode,
                    lang=lang,
                    kind="audio",
                    status=TaskStatus.DONE,
                    target_path=str(target),
                    series_title="Тайтл",
                )
            )
        return target

    def _pending(self, translation_id: int, lang: str, episode: float):
        """Запись о переводе, для которого мы спрашиваем «уже есть ли звук»."""
        with session_scope() as session:
            session.add(
                DownloadTask(
                    translation_id=translation_id,
                    series_id=self.SERIES,
                    episode=episode,
                    lang=lang,
                    kind="video",
                    status=TaskStatus.QUEUED,
                    series_title="Тайтл",
                )
            )

    def test_other_japanese_translation_counts_as_present(self, tmp_path) -> None:
        """Скачали японский звук с равки — с субтитров он уже не нужен."""
        raw = self._done_audio(tmp_path, 771001, "ja", 1.0)
        self._pending(771002, "ja", 1.0)

        assert existing_audio_path(771002) == raw

    def test_another_episode_is_not_confused(self, tmp_path) -> None:
        self._done_audio(tmp_path, 771011, "ja", 1.0)
        self._pending(771012, "ja", 2.0)

        assert existing_audio_path(771012) is None

    def test_russian_dubs_are_kept_apart(self, tmp_path) -> None:
        """Две озвучки одной серии — разные студии, а не дубль."""
        self._done_audio(tmp_path, 771021, "ru", 1.0)
        self._pending(771022, "ru", 1.0)

        assert existing_audio_path(771022) is None

    def test_japanese_does_not_cover_russian(self, tmp_path) -> None:
        self._done_audio(tmp_path, 771031, "ja", 1.0)
        self._pending(771032, "ru", 1.0)

        assert existing_audio_path(771032) is None
