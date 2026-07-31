"""Пересечение корней библиотеки, фонотеки и временной папки.

Совпадающие или вложенные пути ломают вещи, которые чинятся только руками:
сканер заводит `.part`-файлы как серии, а звук попадает в видеобиблиотеку.
Поэтому запрещаем на входе — тесты про то, что запрет не обойти опечаткой.
"""

from __future__ import annotations

import pytest

from app.core.settings_registry import check_path_layout

GOOD = {
    "series_root": "/media/anime",
    "movies_root": "/media/anime_movies",
    "audio_root": "/media/audio",
    "download_tmp": "/data/tmp",
}


class TestValidLayout:
    def test_separate_roots_pass(self):
        assert check_path_layout(GOOD) == []

    def test_empty_values_are_ignored(self):
        # Не заданный путь — это «возьми значение по умолчанию», а не конфликт.
        assert check_path_layout({**GOOD, "audio_root": ""}) == []

    def test_similar_prefix_is_not_nesting(self):
        # `/media/anime_movies` начинается на `/media/anime`, но лежит рядом,
        # а не внутри. Сравнение по строке дало бы ложное срабатывание.
        assert check_path_layout(GOOD) == []


class TestRejected:
    def test_identical_paths(self):
        problems = check_path_layout({**GOOD, "download_tmp": "/media/anime"})
        assert len(problems) == 1
        assert "один путь" in problems[0]

    def test_trailing_slash_does_not_hide_a_clash(self):
        problems = check_path_layout({**GOOD, "download_tmp": "/media/anime/"})
        assert len(problems) == 1

    def test_tmp_inside_the_library(self):
        # Самый вероятный случай: «пусть временная будет рядом с библиотекой».
        # Сканер увидит недокачанные файлы и заведёт их как серии.
        problems = check_path_layout({**GOOD, "download_tmp": "/media/anime/tmp"})
        assert len(problems) == 1
        assert "вложенность" in problems[0]

    def test_audio_inside_the_library(self):
        # Ровно то, чего избегали, заводя фонотеке отдельный корень.
        problems = check_path_layout({**GOOD, "audio_root": "/media/anime/audio"})
        assert len(problems) == 1

    def test_library_inside_tmp(self):
        # Вложенность в обратную сторону тоже недопустима.
        problems = check_path_layout({**GOOD, "download_tmp": "/media"})
        assert len(problems) >= 1

    def test_all_conflicts_are_reported_at_once(self):
        # Показать одну проблему из трёх значило бы заставить исправлять их по
        # очереди, каждый раз получая новый отказ.
        problems = check_path_layout(
            {
                "series_root": "/media/x",
                "movies_root": "/media/x",
                "audio_root": "/media/x",
                "download_tmp": "/data/tmp",
            }
        )
        assert len(problems) == 3


class TestStartupRefusesOverlap:
    """Отказ при старте, а не только на эндпоинте настроек.

    Проверка в `PUT /api/settings` закрывает лишь правку из UI. Значение той же
    настройки может прийти из docker-compose, и такой запуск обязан падать —
    иначе пересекающиеся пути живут молча до первой раскладки файлов.
    """

    def test_bootstrap_refuses_overlapping_paths(self):
        # Пишем именно в таблицу, а не через set_db_override: bootstrap первым
        # делом вызывает load_settings_from_db(), и оно заменяет слой UI целиком
        # содержимым таблицы. Подмена в памяти была бы смыта до проверки.
        from app.core.bootstrap import bootstrap
        from app.core.db import session_scope
        from app.models import AppSetting

        with session_scope() as session:
            session.add(AppSetting(key="series_root", value="/media/anime"))
            session.add(AppSetting(key="download_tmp", value="/media/anime/tmp"))
        try:
            with pytest.raises(RuntimeError, match="пересекаются"):
                bootstrap()
        finally:
            with session_scope() as session:
                for key in ("series_root", "download_tmp"):
                    row = session.get(AppSetting, key)
                    if row is not None:
                        session.delete(row)
            bootstrap()  # возвращаем слой настроек в исходное состояние

    def test_env_layer_is_not_bypassed(self):
        """Отдельным процессом: слой ENV читается при импорте, подменить его
        внутри уже запущенного теста нельзя."""
        import os
        import subprocess
        import sys

        env = {
            **os.environ,
            "A365_SERIES_ROOT": "/srv/media/anime",
            "A365_DOWNLOAD_TMP": "/srv/media/anime/tmp",
        }
        result = subprocess.run(
            [sys.executable, "-c", "from app.core.bootstrap import bootstrap; bootstrap()"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert result.returncode != 0, "запуск с пересекающимися путями в ENV не упал"
        assert "RuntimeError" in result.stderr
