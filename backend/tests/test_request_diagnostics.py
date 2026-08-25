"""Отказ запроса к Anime365 обязан объяснять себя.

Заведено после разбора живого сервера. Таймауты к `smotret-anime.org` шли
сутками, а в логе стояло ровно одно: «Зеркало недоступно: ConnectTimeout». По
этой строке нельзя отличить

* оборванную сеть — от запроса, ушедшего **мимо** прокси (`NO_PROXY`, чужая
  схема, незаданная переменная);
* сеть, которая молчит десять секунд, — от очереди лимитера, съевшей их до
  запроса;
* одинокий запрос — от сорока одновременных, задавивших прокси.

Полдня ушло на проверку сети, которая оказалась исправной: из того же
контейнера настоящий запрос переводов с рабочим токеном прошёл шесть раз из
шести. Теперь всё это пишется сразу.

⚠️ Отдельно про правило №0: адрес прокси попадает в лог, а в нём бывают логин и
пароль. Тест на маскировку ниже — не формальность.
"""

from __future__ import annotations

import asyncio
import logging

from urllib.parse import urlsplit

import httpx
import pytest

from app.core import proxy
from app.core.config import settings
from app.services import anime365

PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
URL = "https://smotret-anime.org/api/series"


@pytest.fixture
def clean_env(monkeypatch):
    """Переменные прокси на время теста — только те, что задал сам тест."""
    for name in PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    return monkeypatch


def _ours(caplog) -> list[logging.LogRecord]:
    """Только записи клиента Anime365.

    `caplog.at_level(..., logger=...)` поднимает уровень нужному логгеру, но
    ловит всё подряд: рядом пишет `app.core.config` про base_url вне allowlist.
    Без этой выборки тесты падали на чужой строке.
    """
    return [r for r in caplog.records if r.name == anime365.log.name]


class TestRouteFor:
    def test_no_proxy_at_all(self, clean_env):
        assert "напрямую" in proxy.route_for(URL)

    def test_https_proxy_is_used(self, clean_env):
        clean_env.setenv("HTTPS_PROXY", "http://proxy.local:20171")
        assert proxy.route_for(URL) == "http://proxy.local:20171"

    def test_all_proxy_is_the_fallback(self, clean_env):
        clean_env.setenv("ALL_PROXY", "socks5h://proxy.local:20170")
        assert proxy.route_for(URL) == "socks5h://proxy.local:20170"

    def test_http_proxy_does_not_cover_https(self, clean_env):
        """Классическая ловушка: задан HTTP_PROXY, а весь трафик к сайту — https."""
        clean_env.setenv("HTTP_PROXY", "http://proxy.local:20171")
        route = proxy.route_for(URL)
        assert "напрямую" in route and "https" in route

    def test_no_proxy_list_is_respected(self, clean_env):
        clean_env.setenv("HTTPS_PROXY", "http://proxy.local:20171")
        clean_env.setenv("NO_PROXY", "smotret-anime.org")
        assert "напрямую" in proxy.route_for(URL)
        assert "smotret-anime.org" in proxy.route_for(URL)

    def test_credentials_never_reach_the_log(self, clean_env):
        """Правило №0: адрес идёт в лог, пароль в нём — утечка."""
        clean_env.setenv("ALL_PROXY", "socks5h://вася:очень-секретный-пароль@proxy.local:20170")
        route = proxy.route_for(URL)
        assert "очень-секретный-пароль" not in route
        assert "вася" not in route
        assert "proxy.local:20170" in route, "адрес обязан остаться читаемым"


class _FailingClient:
    """Клиент, который падает так же, как падал на сервере."""

    async def get(self, url, params=None):
        raise httpx.ConnectTimeout("")

    async def aclose(self) -> None:
        pass


class _WorkingClient:
    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"data": {"isLogined": True}}

    async def get(self, url, params=None):
        return self._Response()

    async def aclose(self) -> None:
        pass


def _call(fake, monkeypatch):
    monkeypatch.setattr(
        settings, "get_str", lambda key: {"access_token": "", "user_agent": "test"}.get(key, "")
    )
    client = anime365.Anime365Client()
    client._client = fake  # noqa: SLF001
    return client


class TestFailureExplainsItself:
    def test_the_line_carries_everything_needed(self, monkeypatch, caplog, clean_env):
        clean_env.setenv("HTTPS_PROXY", "http://proxy.local:20171")
        client = _call(_FailingClient(), monkeypatch)

        with caplog.at_level(logging.WARNING, logger=anime365.log.name):
            with pytest.raises(anime365.Anime365Error):
                asyncio.run(client.me())

        message = _ours(caplog)[0].getMessage()
        assert "ConnectTimeout" in message
        assert "/api/me" in message, "без пути непонятно, какой запрос упал"
        assert "мс" in message, "без времени неясно, упёрлись в таймаут или отказали сразу"
        assert "http://proxy.local:20171" in message, "маршрут — главное, чего не хватало"
        assert "зеркало 1 из" in message
        assert "одновременных запросов" in message

    def test_it_says_when_the_request_went_direct(self, monkeypatch, caplog, clean_env):
        """Отдельный случай: прокси задан, но к этому адресу не применяется.

        ⚠️ Хост берём у самого клиента, а не пишем руками. Первое зеркало —
        не обязательно `smotret-anime.org`: при пустом `base_url` выбирается
        первое из allowlist, и прибитый в тесте домен просто не совпал бы с
        тем, куда пошёл запрос. На этом тест и попался.
        """
        client = _call(_FailingClient(), monkeypatch)
        first = urlsplit(client._ordered_origins()[0]).hostname  # noqa: SLF001

        clean_env.setenv("HTTPS_PROXY", "http://proxy.local:20171")
        clean_env.setenv("NO_PROXY", first or "")

        with caplog.at_level(logging.WARNING, logger=anime365.log.name):
            with pytest.raises(anime365.Anime365Error):
                asyncio.run(client.me())

        assert "напрямую" in _ours(caplog)[0].getMessage()

    def test_the_password_does_not_leak_into_the_log(self, monkeypatch, caplog, clean_env):
        clean_env.setenv("ALL_PROXY", "socks5h://вася:очень-секретный-пароль@proxy.local:20170")
        client = _call(_FailingClient(), monkeypatch)

        with caplog.at_level(logging.WARNING, logger=anime365.log.name):
            with pytest.raises(anime365.Anime365Error):
                asyncio.run(client.me())

        assert "очень-секретный-пароль" not in caplog.text


class TestSuccessIsQuietButMeasurable:
    def test_a_good_request_says_nothing_at_warning(self, monkeypatch, caplog, clean_env):
        client = _call(_WorkingClient(), monkeypatch)

        with caplog.at_level(logging.WARNING, logger=anime365.log.name):
            asyncio.run(client.me())

        assert _ours(caplog) == [], "успешный запрос не должен ничего писать в обычный лог"

    def test_at_debug_it_reports_the_timing(self, monkeypatch, caplog, clean_env):
        """Включается по требованию: A365_LOG_LEVELS=app.services.anime365=DEBUG."""
        client = _call(_WorkingClient(), monkeypatch)

        with caplog.at_level(logging.DEBUG, logger=anime365.log.name):
            asyncio.run(client.me())

        messages = [r.getMessage() for r in _ours(caplog)]
        assert any("/api/me" in m and "мс" in m for m in messages)


class TestInflightCounter:
    def test_it_returns_to_zero(self, monkeypatch, clean_env):
        """Счётчик обязан убираться за собой — и после успеха, и после отказа."""
        before = len(anime365._inflight)  # noqa: SLF001

        client = _call(_WorkingClient(), monkeypatch)
        asyncio.run(client.me())
        assert len(anime365._inflight) == before  # noqa: SLF001

        client = _call(_FailingClient(), monkeypatch)
        with pytest.raises(anime365.Anime365Error):
            asyncio.run(client.me())
        assert len(anime365._inflight) == before  # noqa: SLF001
