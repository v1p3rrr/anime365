"""Строка доступа со временем ответа.

⚠️ Заведено по живой истории. Пользователь сообщил, что библиотека грузится
«несколько десятков секунд», а проверить это было нечем: uvicorn пишет строку
доступа БЕЗ длительности. Пришлось искать косвенно — по паузам между проверками
здоровья контейнера, — и вывод получился ненадёжным: пауза доказывает лишь то,
что процесс был чем-то занят, но не то, сколько ждал человек.

Настоящее подтверждение нашлось в разнице между временем соседних строк:
запрос `POST /api/catalog/cards` в 22:02:05 завершился в 22:02:27. Двадцать две
секунды, и увидеть их можно было только сложением в уме.

Теперь длительность пишется явно, для **каждого** запроса — не только для API,
но и для статики и SPA-маршрутов: middleware самое внешнее.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import middleware


def _app(handler) -> FastAPI:
    app = FastAPI()
    app.get("/api/thing")(handler)
    app.get("/api/health")(handler)
    app.add_middleware(middleware.AccessLogMiddleware)
    return app


def _quick() -> dict:
    return {"ok": True}


class TestEveryRequestIsTimed:
    def test_line_carries_method_path_status_and_duration(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="app.access"):
            TestClient(_app(_quick)).get("/api/thing")

        line = caplog.records[-1].getMessage()
        assert "GET" in line
        assert "/api/thing" in line
        assert "200" in line
        assert " мс" in line, "без длительности строка бессмысленна"

    def test_query_string_is_kept(self, caplog) -> None:
        """Каталог различается только параметрами — без них строки одинаковы."""
        with caplog.at_level(logging.INFO, logger="app.access"):
            TestClient(_app(_quick)).get("/api/thing?local=only&sort=score")

        assert "local=only&sort=score" in caplog.records[-1].getMessage()

    def test_static_and_spa_are_timed_too(self, caplog) -> None:
        """Не только API: «страница грузится долго» бывает и про бандл."""
        app = FastAPI()
        app.get("/assets/index.js")(_quick)
        app.add_middleware(middleware.AccessLogMiddleware)

        with caplog.at_level(logging.INFO, logger="app.access"):
            TestClient(app).get("/assets/index.js")

        assert "/assets/index.js" in caplog.records[-1].getMessage()


class TestWhatGetsAttention:
    def test_slow_request_is_a_warning(self, caplog, monkeypatch) -> None:
        """Медленное должно быть видно в логе, где уровень поднят до WARNING."""
        monkeypatch.setattr(middleware, "SLOW_REQUEST_MS", 0)

        with caplog.at_level(logging.INFO, logger="app.access"):
            TestClient(_app(_quick)).get("/api/thing")

        assert caplog.records[-1].levelno == logging.WARNING

    def test_health_checks_are_quiet(self, caplog, monkeypatch) -> None:
        """Их тридцать в час, и они ни о чём не говорят, пока не тормозят."""
        monkeypatch.setattr(middleware, "SLOW_REQUEST_MS", 10_000)

        with caplog.at_level(logging.INFO, logger="app.access"):
            TestClient(_app(_quick)).get("/api/health")

        assert [r for r in caplog.records if r.name == "app.access"] == []

    def test_a_slow_health_check_is_not_quiet(self, caplog, monkeypatch) -> None:
        """Заглушено «в норме», а не «всегда»: зависший health — это симптом."""
        monkeypatch.setattr(middleware, "SLOW_REQUEST_MS", 0)

        with caplog.at_level(logging.INFO, logger="app.access"):
            TestClient(_app(_quick)).get("/api/health")

        assert caplog.records[-1].levelno == logging.WARNING

    def test_a_crash_is_still_timed(self, caplog) -> None:
        """Упавший через тридцать секунд запрос и упавший сразу — разные беды."""

        def boom() -> dict:
            raise RuntimeError("сломалось")

        with caplog.at_level(logging.INFO, logger="app.access"):
            try:
                TestClient(_app(boom)).get("/api/thing")
            except RuntimeError:
                pass

        line = caplog.records[-1].getMessage()
        assert "исключение" in line
        assert " мс" in line
