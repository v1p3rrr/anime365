"""Хост сверяется на каждом шаге редиректа, а не только у исходной ссылки.

Проверять только первый адрес мало, и это не теория. Ссылку выдаёт чужой API,
а `follow_redirects=True` уводит клиент туда, куда скажет ответ. Подменённый или
взломанный upstream отвечает `302` на `http://192.168.1.1/` — и приложение
сходит туда **из локальной сети сервера**, потому что локальные диапазоны в
типовой раскладке стоят в `NO_PROXY` и идут мимо прокси. Тело при этом уедет
в браузер через `StreamingResponse`.

Соседний эндпоинт скачивания делал это правильно с самого начала
(`follow_redirects=False` и проверка `Location`), а поток и субтитры — нет.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.anime365 import Anime365Error
from app.services.downloader import (
    MAX_REDIRECT_HOPS,
    UnknownHostError,
    is_allowed_target,
    next_hop,
    open_checked,
)

ALLOWED = "https://smotret-anime.org/translations/mp4/1"
CDN = "https://moon-q1.b-cdn.net/route/?m=1&r=2"

#: Куда уводить не должны ни при каких обстоятельствах.
INTERNAL = "http://192.168.1.1/admin"


def _client(handler) -> httpx.AsyncClient:
    """Клиент строго без автоматических редиректов — как в рабочем коде."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _redirect_to(target: str, status_code: int = 302):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == ALLOWED:
            return httpx.Response(status_code, headers={"location": target})
        return httpx.Response(200, text="содержимое")

    return handler


async def _run(handler, url: str = ALLOWED) -> httpx.Response:
    async with _client(handler) as client:
        return await open_checked(client, url, {}, stream=False)


class TestRedirectIsChecked:
    def test_redirect_into_the_local_network_is_refused(self) -> None:
        with pytest.raises(Anime365Error) as exc:
            asyncio.run(_run(_redirect_to(INTERNAL)))

        # Внутренний адрес — не «новый CDN»: отказ есть, подсказки про настройку нет.
        assert isinstance(exc.value, UnknownHostError)
        assert exc.value.fixable is False

    def test_redirect_to_a_stranger_is_refused(self) -> None:
        with pytest.raises(Anime365Error):
            asyncio.run(_run(_redirect_to("https://evil.example.com/x")))

    def test_redirect_to_the_cdn_is_followed(self) -> None:
        response = asyncio.run(_run(_redirect_to(CDN)))

        assert response.status_code == 200
        assert response.text == "содержимое"

    def test_every_redirect_status_is_checked(self) -> None:
        """301, 303, 307, 308 — не только 302."""
        for code in (301, 303, 307, 308):
            with pytest.raises(Anime365Error):
                asyncio.run(_run(_redirect_to(INTERNAL, status_code=code)))


class TestRelativeLocation:
    """`Location` по стандарту бывает относительным.

    Сравнивать его с allowlist напрямую бессмысленно — хоста в нём нет вовсе,
    и законный редирект отвергался бы как посторонний.
    """

    def test_relative_location_stays_on_the_same_host(self) -> None:
        response = asyncio.run(_run(_redirect_to("/other/path")))

        assert response.status_code == 200

    def test_next_hop_resolves_against_the_current_url(self) -> None:
        assert next_hop("https://smotret-anime.org/a/b", "/c") == "https://smotret-anime.org/c"

    def test_next_hop_keeps_an_absolute_location(self) -> None:
        assert next_hop("https://smotret-anime.org/a", INTERNAL) == INTERNAL


class TestBoundaries:
    def test_the_first_url_is_checked_too(self) -> None:
        with pytest.raises(Anime365Error):
            asyncio.run(_run(lambda request: httpx.Response(200), url=INTERNAL))

    def test_an_endless_chain_gives_up(self) -> None:
        """Петля не должна крутиться вечно."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": CDN})

        with pytest.raises(Anime365Error) as exc:
            asyncio.run(_run(handler))

        assert "цепочка редиректов" in str(exc.value)

    def test_redirect_without_location_is_an_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302)

        with pytest.raises(Anime365Error):
            asyncio.run(_run(handler))

    def test_the_hop_limit_is_not_absurd(self) -> None:
        assert 2 <= MAX_REDIRECT_HOPS <= 10

    def test_a_following_client_does_not_disarm_the_check(self) -> None:
        """⚠️ Защита не должна зависеть от того, как создан клиент.

        `client.send()` у клиента с `follow_redirects=True` проходит цепочку
        сам и возвращает финальный ответ. Цикл проверки увидел бы
        `is_redirect == False` и отдал бы наружу непроверенный хост, ничего не
        заметив. Поэтому флаг задаётся на самом вызове, а не только при
        создании клиента.
        """
        handler = _redirect_to(INTERNAL)

        async def run():
            # Клиент нарочно «неправильный» — со следованием редиректов.
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler), follow_redirects=True
            ) as client:
                return await open_checked(client, ALLOWED, {}, stream=False)

        with pytest.raises(Anime365Error):
            asyncio.run(run())


class TestAllowlistItself:
    def test_mirrors_and_cdn_pass(self) -> None:
        assert is_allowed_target(ALLOWED)
        assert is_allowed_target(CDN)

    def test_private_addresses_do_not_pass(self) -> None:
        assert not is_allowed_target(INTERNAL)
        assert not is_allowed_target("http://127.0.0.1:8080/api/settings")
        assert not is_allowed_target("http://169.254.169.254/latest/meta-data/")

    def test_lookalike_host_does_not_pass(self) -> None:
        """Суффикс проверяется с точкой, иначе `evilb-cdn.net` сошёл бы за свой."""
        assert not is_allowed_target("https://evilb-cdn.net/x")
        assert not is_allowed_target("https://smotret-anime.org.evil.com/x")
