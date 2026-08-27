"""Курсор пагинации обязан двигаться вперёд.

⚠️ Обход каталога и обход переводов идут через `afterId`, и оба берут курсор из
последней записи ответа. Держится это на том, что сайт вернёт записи по
возрастанию id. У переводов такое уже ломалось: без `feed=id` выдача не
отсортирована (`claude/02-api-findings.md`).

Не сдвинувшийся курсор означал бы **вечный цикл**: тот же запрос, тот же ответ.
У каталога это ещё и тысяча строк, переписываемых на каждом обороте, и флаг
«синхронизация идёт», который снимается в `finally` — то есть никогда.

Здесь проверяется и защита, и то, что на рабочем пути она не мешает.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.anime365 import MAX_LIMIT, Anime365Client


def run(coro):
    return asyncio.run(coro)


def _client(handler) -> Anime365Client:
    transport = httpx.MockTransport(handler)
    return Anime365Client(httpx.AsyncClient(transport=transport, follow_redirects=False))


def _page(ids: list[int]) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": i} for i in ids]})


class TestCatalogWalkStops:
    """Обход каталога прекращается, когда курсор перестал двигаться."""

    async def _collect(self, handler, limit: int = 20) -> list[list[int]]:
        client = _client(handler)
        out: list[list[int]] = []
        try:
            async for batch in client.iter_catalog(after_id=0):
                out.append([item["id"] for item in batch])
                if len(out) > limit:
                    pytest.fail("обход не прекратился — это и есть вечный цикл")
        finally:
            await client.__aexit__()
        return out

    def test_a_stuck_cursor_does_not_loop_forever(self) -> None:
        # Сайт упорно отдаёт одну и ту же страницу: курсор не сдвигается.
        #
        # ⚠️ Первый оборот законно сдвигает курсор с 0 на минимальный id в
        # ответе, поэтому застревание видно только со второго. Проверяем то,
        # что здесь и важно, — что обход **конечен**, а не число страниц.
        def handler(_request: httpx.Request) -> httpx.Response:
            return _page([5, 3, 1])

        pages = run(self._collect(handler))
        assert len(pages) == 2, "обход обязан прекратиться, а не идти вечно"

    def test_a_normal_walk_is_untouched(self) -> None:
        # Обратная сторона: пока ответ отсортирован, обход идёт как прежде.
        pages = {0: [1, 2, 3], 3: [4, 5, 6], 6: []}

        def handler(request: httpx.Request) -> httpx.Response:
            after = int(request.url.params.get("afterId", 0))
            return _page(pages[after])

        assert run(self._collect(handler)) == [[1, 2, 3], [4, 5, 6]]

    def test_an_empty_answer_ends_the_walk(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _page([])

        assert run(self._collect(handler)) == []


class TestTranslationsWalkStops:
    """То же у переводов, и здесь это опаснее всего.

    Короткая страница обрывает цикл и без нашей проверки — но **полная**
    страница с несдвинувшимся курсором давала бы вечный цикл.
    """

    def _translations(self, handler) -> list[dict]:
        client = _client(handler)
        try:
            return run(client.translations(1))
        finally:
            run(client.__aexit__())

    def test_a_full_page_with_a_stuck_cursor_stops(self) -> None:
        full_page = list(range(MAX_LIMIT, 0, -1))  # ровно страница, по убыванию
        calls = 0

        # ⚠️ Потолок нужен самому тесту: без защиты в коде обход не кончится
        # никогда, и прогон повис бы вместо того, чтобы упасть. После потолка
        # отдаём пустую страницу — цикл завершится, а число вызовов покажет,
        # сработала защита или нет.
        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return _page([] if calls > 20 else full_page)

        # Застревание видно со второго оборота: первый законно сдвигает курсор
        # с нуля на минимальный id в ответе.
        self._translations(handler)
        assert calls == 2, f"обход обязан прекратиться, а сделал запросов: {calls}"

    def test_a_normal_walk_is_untouched(self) -> None:
        first = list(range(1, MAX_LIMIT + 1))

        def handler(request: httpx.Request) -> httpx.Response:
            after = int(request.url.params.get("afterId", 0))
            return _page(first if after == 0 else [MAX_LIMIT + 1])

        got = self._translations(handler)
        assert len(got) == MAX_LIMIT + 1
        assert got[-1]["id"] == MAX_LIMIT + 1

    def test_a_short_page_still_ends_the_walk(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _page([1, 2, 3])

        assert len(self._translations(handler)) == 3
