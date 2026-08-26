"""Три дефекта, найденные ревью после аудита безопасности.

Два первых — продолжение той же истории, что и `test_ssrf_redirects.py`:
проверка хоста на каждом шаге редиректа была добавлена в поток и субтитры, но
**не** в путь загрузок и не в пробы размера. То есть заявленная закрытой дыра
оставалась открытой ровно там, где работа идёт без человека за экраном — в
автоскачивании субтитров по расписанию.

Третий к безопасности отношения не имеет: это гонка двух воркеров за одну
задачу, и кончалась она битым файлом.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.downloads import DownloadTask, TaskStatus
from app.services.anime365 import Anime365Client, Anime365Error
from app.services.downloader import Downloader

INTERNAL = "http://192.168.1.1/admin"
ALLOWED = "https://smotret-anime.org/translations/mp4/1"
CDN = "https://moon-q1.b-cdn.net/route/?m=1&r=2"


def _transport(handler) -> httpx.AsyncClient:
    """Клиент без автоследования — ровно как в рабочем коде после правки."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


class TestDownloadPathChecksRedirects:
    """Путь, который пишет файлы в библиотеку.

    ⚠️ Именно он оставался незакрытым: `Downloader.__aenter__` создавал клиент
    с `follow_redirects=True`, а `_fetch_once` сверял с allowlist только
    исходный адрес.
    """

    def _download(self, handler, tmp_path):
        async def run():
            downloader = Downloader()
            downloader._client = _transport(handler)
            downloader._speed_limit_bps = 0
            try:
                return await downloader._fetch_once(ALLOWED, tmp_path / "серия.mp4")
            finally:
                await downloader._client.aclose()

        return asyncio.run(run())

    def test_redirect_into_the_local_network_is_refused(self, tmp_path) -> None:
        visited: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            visited.append(str(request.url))
            if str(request.url) == ALLOWED:
                return httpx.Response(302, headers={"location": INTERNAL})
            return httpx.Response(200, content=b"\x00" * 1024)

        with pytest.raises(Anime365Error) as exc:
            self._download(handler, tmp_path)

        assert "неизвестный хост" in str(exc.value)
        assert INTERNAL not in visited, "запрос на внутренний адрес не должен уходить вовсе"

    def test_nothing_is_written_to_disk(self, tmp_path) -> None:
        """Отказ обязан случиться ДО того, как появится файл."""

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == ALLOWED:
                return httpx.Response(302, headers={"location": INTERNAL})
            return httpx.Response(200, content=b"\x00" * 1024)

        with pytest.raises(Anime365Error):
            self._download(handler, tmp_path)

        assert list(tmp_path.iterdir()) == []

    def test_a_normal_redirect_to_the_cdn_still_works(self, tmp_path) -> None:
        """Правка не должна ломать обычную загрузку: сайт отвечает 302 на CDN."""

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == ALLOWED:
                return httpx.Response(302, headers={"location": CDN})
            return httpx.Response(
                200, content=b"\x00" * 2048, headers={"content-type": "video/mp4"}
            )

        size = self._download(handler, tmp_path)

        assert size == 2048


class TestSizeProbeChecksHost:
    """Вес файла выясняется до скачивания, и адрес берётся из ответа чужого API.

    ⚠️ `rewrite_host` его не трогает намеренно — CDN-ссылки подписанные и живут
    на своих доменах, — поэтому проверять хост надо в самой пробе.
    """

    def _probe(self, handler, url: str):
        async def run():
            client = _transport(handler)
            try:
                return await Anime365Client(client)._probe_size(url)
            finally:
                await client.aclose()

        return asyncio.run(run())

    def test_a_foreign_host_is_not_probed(self) -> None:
        visited: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            visited.append(str(request.url))
            return httpx.Response(200, headers={"content-length": "123"})

        assert self._probe(handler, INTERNAL) is None
        assert visited == [], "к постороннему хосту не должно уйти ни одного запроса"

    def test_a_redirect_away_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == ALLOWED:
                return httpx.Response(302, headers={"location": INTERNAL})
            return httpx.Response(200, headers={"content-length": "999"})

        assert self._probe(handler, ALLOWED) is None

    def test_a_known_host_is_still_measured(self) -> None:
        """Правка не должна отнять саму возможность узнать вес."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-length": "83960238"})

        assert self._probe(handler, CDN) == 83960238

    def test_range_is_the_fallback_when_head_says_nothing(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "HEAD":
                return httpx.Response(405)
            return httpx.Response(206, headers={"content-range": "bytes 0-0/4242"})

        assert self._probe(handler, CDN) == 4242


SERIES = 890_777


def _queued(translation_id: int) -> int:
    from app.services.download_queue import _next_position

    with session_scope() as session:
        if session.get(Series, SERIES) is None:
            session.add(Series(id=SERIES, title="Гонка", type="tv"))
            session.flush()
        task = DownloadTask(
            translation_id=translation_id,
            series_id=SERIES,
            kind="video",
            status=TaskStatus.QUEUED,
            series_title="Гонка",
            created_by_user_id=1,
            active_key=0,
            position=_next_position(session),
            created_at=datetime(2026, 8, 26, 12, 0, 0),
        )
        session.add(task)
        session.flush()
        return task.id


class TestClaimIsAtomic:
    """Задача не должна достаться двум воркерам сразу.

    ⚠️ Кончалась такая гонка не дублем в списке, а **битым файлом**: загрузка
    пишет в `.part`, открытый на дописывание, и смещение каждый воркер считает
    сам. Двое дописывают перекрывающиеся куски в один файл, а задача при этом
    завершается успехом — то есть молча.

    Саму параллельность здесь не изображаем: потоки с SQLite дали бы плавающий
    тест. Проверяется свойство, из которого гонка и следовала, — что пометка
    задачи выполняется **условно** и второй раз не срабатывает.
    """

    def _queue(self):
        from app.services.download_queue import DownloadQueue

        return DownloadQueue()

    def test_a_claimed_task_is_not_handed_out_again(self) -> None:
        task_id = _queued(890_101)
        queue = self._queue()

        first = queue._claim_next()
        while first is not None and first != task_id:
            first = queue._claim_next()

        assert first == task_id
        assert queue._claim_next() != task_id

    def test_two_claims_take_two_different_tasks(self) -> None:
        first_id = _queued(890_102)
        second_id = _queued(890_103)
        queue = self._queue()

        claimed = set()
        for _ in range(20):
            got = queue._claim_next()
            if got is None:
                break
            claimed.add(got)
            if {first_id, second_id} <= claimed:
                break

        assert first_id in claimed
        assert second_id in claimed
        assert len(claimed) == len(set(claimed))

    def test_claiming_marks_the_task_downloading(self) -> None:
        task_id = _queued(890_104)
        queue = self._queue()

        got = queue._claim_next()
        while got is not None and got != task_id:
            got = queue._claim_next()

        with session_scope() as session:
            assert session.get(DownloadTask, task_id).status == TaskStatus.DOWNLOADING

    def test_empty_queue_gives_nothing(self) -> None:
        queue = self._queue()
        while queue._claim_next() is not None:
            pass

        assert queue._claim_next() is None
