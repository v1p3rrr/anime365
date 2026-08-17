"""Уровни логирования: файл `config/logging.yaml` и переменные окружения.

Раньше уровень нельзя было поменять никак: `setup_logging()` звался без
аргументов и всегда ставил INFO. Чтобы посмотреть, почему папка села не на тот
тайтл, приходилось править код и пересобирать образ.

⚠️ Конфиг намеренно узкий — только уровни, не `dictConfig`. Полный конфиг
позволил бы заменить обработчик, а к нашему прицеплен фильтр, вырезающий
`access_token`. Токен Anime365 бессрочен, поэтому одна невнимательная строка в
YAML превратила бы лог в утечку.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core import logging_setup
from app.core.logging_setup import load_levels, parse_levels, setup_logging


class TestParsingTheEnvironment:
    def test_pairs_are_read(self) -> None:
        assert parse_levels("app.access=DEBUG,httpx=WARNING") == {
            "app.access": "DEBUG",
            "httpx": "WARNING",
        }

    def test_case_and_spaces_do_not_matter(self) -> None:
        assert parse_levels(" app.access = debug ") == {"app.access": "DEBUG"}

    def test_nonsense_is_skipped_not_fatal(self) -> None:
        """Мусор в переменной окружения — не повод не запуститься."""
        assert parse_levels("app.access=БОЛТЛИВО,httpx=ERROR") == {"httpx": "ERROR"}

    def test_empty_string_is_empty(self) -> None:
        assert parse_levels("") == {}


class TestReadingTheFile:
    def _write(self, tmp_path: Path, text: str, monkeypatch) -> None:
        path = tmp_path / "logging.yaml"
        path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(logging_setup, "CONFIG_PATH", path)
        monkeypatch.setattr(logging_setup, "REPO_PATH", tmp_path / "нет-такого.yaml")

    def test_root_and_loggers(self, tmp_path: Path, monkeypatch) -> None:
        self._write(
            tmp_path,
            "root: WARNING\nloggers:\n  app.access: DEBUG\n  httpx: ERROR\n",
            monkeypatch,
        )

        assert load_levels() == ("WARNING", {"app.access": "DEBUG", "httpx": "ERROR"})

    def test_a_missing_file_is_not_an_error(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(logging_setup, "CONFIG_PATH", tmp_path / "нет.yaml")
        monkeypatch.setattr(logging_setup, "REPO_PATH", tmp_path / "тоже-нет.yaml")

        assert load_levels() == (None, {})

    def test_broken_yaml_does_not_stop_the_app(self, tmp_path: Path, monkeypatch) -> None:
        """Без логов разбираться в поломке нечем — значит логи обязаны подняться."""
        self._write(tmp_path, "root: [не закрыт\n", monkeypatch)

        assert load_levels() == (None, {})

    def test_unknown_level_is_ignored(self, tmp_path: Path, monkeypatch) -> None:
        self._write(tmp_path, "root: ГРОМКО\nloggers:\n  httpx: ТИХО\n", monkeypatch)

        assert load_levels() == (None, {})


class TestWhatActuallyGetsApplied:
    def test_the_file_sets_levels(self, tmp_path: Path, monkeypatch) -> None:
        path = tmp_path / "logging.yaml"
        path.write_text("root: INFO\nloggers:\n  app.access: WARNING\n", encoding="utf-8")
        monkeypatch.setattr(logging_setup, "CONFIG_PATH", path)
        monkeypatch.delenv("A365_LOG_LEVEL", raising=False)
        monkeypatch.delenv("A365_LOG_LEVELS", raising=False)

        try:
            setup_logging()
            assert logging.getLogger("app.access").level == logging.WARNING
        finally:
            _restore()

    def test_environment_beats_the_file(self, tmp_path: Path, monkeypatch) -> None:
        """Разовое перекрытие «на один запуск» не должно требовать правки файла."""
        path = tmp_path / "logging.yaml"
        path.write_text("root: INFO\nloggers:\n  app.access: WARNING\n", encoding="utf-8")
        monkeypatch.setattr(logging_setup, "CONFIG_PATH", path)
        monkeypatch.setenv("A365_LOG_LEVELS", "app.access=DEBUG")

        try:
            setup_logging()
            assert logging.getLogger("app.access").level == logging.DEBUG
        finally:
            _restore()

    def test_httpx_stays_quiet_by_default(self, tmp_path: Path, monkeypatch) -> None:
        """Он логирует полные URL, а в них уходит access_token."""
        monkeypatch.setattr(logging_setup, "CONFIG_PATH", tmp_path / "нет.yaml")
        monkeypatch.setattr(logging_setup, "REPO_PATH", tmp_path / "тоже-нет.yaml")
        monkeypatch.delenv("A365_LOG_LEVELS", raising=False)

        try:
            setup_logging()
            assert logging.getLogger("httpx").level == logging.WARNING
        finally:
            _restore()


def _restore() -> None:
    """Возвращаем логирование в исходное состояние — иначе поедут соседние тесты."""
    for name in ("app.access", "httpx", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.NOTSET)
    logging.getLogger().setLevel(logging.WARNING)
