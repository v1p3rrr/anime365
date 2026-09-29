"""Скачивание на компьютер: с сервера — своё, с Anime365 — напрямую.

Главное здесь то же, что и у проксирования потока: **токен не должен попасть в
браузер**. Разница в том, что видео человеку отдаёт не наш сервер, а CDN, и
единственная ниточка между ними — заголовок `Location`, который мы забираем
сами и проверяем перед тем, как отдать.

Проверено на живом API (2026-08-04): `translations/mp4/{id}` с токеном отвечает
302, в `Location` стоит `https://moon-*.b-cdn.net/route/?m=…&r=…` — токена там
нет, а CDN сам присылает `content-disposition` с именем файла и `accept-ranges`.
Тесты закрепляют именно наши решения на каждый возможный ответ.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.api.stream import anime365_style_name, attachment_headers


class TestAttachmentHeaders:
    """Имя файла обязано пережить кириллицу и японский."""

    def test_plain_name_is_quoted(self) -> None:
        header = attachment_headers("video.mp4")["Content-Disposition"]
        assert 'filename="video.mp4"' in header
        assert "filename*=UTF-8''video.mp4" in header

    def test_cyrillic_survives_in_the_rfc_form(self) -> None:
        header = attachment_headers("Зомбилэнд.ass")["Content-Disposition"]
        # В голом виде не-ASCII вырезается: гадать с транслитерацией не наше дело.
        assert 'filename="_________.ass"' in header
        # А в RFC-виде имя приезжает целиком, и его понимает любой браузер.
        assert "filename*=UTF-8''%D0%97" in header

    def test_quotes_cannot_break_the_header(self) -> None:
        header = attachment_headers('a"b.mp4')["Content-Disposition"]
        assert header.count('"') == 2, "кавычка из имени сломала бы разбор заголовка"


class TestAnime365StyleName:
    """Имя совпадает с тем, которым сайт называет свой же видеофайл."""

    def test_falls_back_when_translation_is_unknown(self) -> None:
        assert anime365_style_name(999999999, ".ass") == "anime365_999999999.ass"

    def test_uses_romaji_and_padded_number(self) -> None:
        from app.core.db import session_scope
        from app.models.catalog import Episode, Series, Translation

        with session_scope() as session:
            session.merge(Series(id=90001, titles={"romaji": "Sousou no Frieren"}, title="x"))
            session.merge(Episode(id=90002, series_id=90001, episode_int="6", episode_type="tv"))
            session.merge(Translation(id=90003, series_id=90001, episode_id=90002))

        assert anime365_style_name(90003, ".ass") == "[Anime365] Sousou no Frieren - 06 (t90003).ass"


class TestDirectDownload:
    """Что мы делаем с каждым возможным ответом Anime365."""

    TOKEN = "0123456789abcdef" * 4
    SIGNED = "https://moon-s665139.b-cdn.net/route/?m=abc&r=def"

    @pytest.fixture
    def _embed(self, monkeypatch):
        """Подменяем поход в сеть: нас интересует только разбор ответа."""
        from app.services.anime365 import EmbedInfo

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def embed(self, translation_id: int):
                return EmbedInfo(
                    translation_id=translation_id,
                    download=[
                        {
                            "height": 1080,
                            "url": f"https://smotret-anime.app/translations/mp4/1?access_token={TestDirectDownload.TOKEN}",
                        }
                    ],
                    stream=[],
                    subtitles_url=None,
                    embed_url=None,
                )

        monkeypatch.setattr("app.api.stream.Anime365Client", FakeClient)

    def _respond(self, monkeypatch, status: int, headers: dict[str, str]) -> None:
        class FakeHttp:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, **kwargs):
                return httpx.Response(status, headers=headers, request=httpx.Request("GET", url))

        monkeypatch.setattr("app.api.stream.httpx.AsyncClient", lambda **kw: FakeHttp())

    def test_redirects_to_the_signed_cdn_link(self, monkeypatch, _embed) -> None:
        from app.api.stream import download_video

        self._respond(monkeypatch, 302, {"location": self.SIGNED})
        response = asyncio.run(download_video(1, height=1080))

        assert response.status_code == 302
        assert response.headers["location"] == self.SIGNED

    def test_token_in_the_location_is_refused(self, monkeypatch, _embed) -> None:
        """Страховка на случай, если сайт однажды начнёт прокидывать токен.

        Показать премиум-токен браузеру хуже, чем не дать скачать файл: токен
        бессрочный, а отказ человек увидит сразу и пожалуется.
        """
        from app.api.stream import download_video
        from app.core.config import settings
        from app.core.errors import ApiError

        settings.set_db_override("access_token", self.TOKEN)
        try:
            self._respond(
                monkeypatch, 302, {"location": f"{self.SIGNED}&access_token={self.TOKEN}"}
            )
            with pytest.raises(ApiError) as exc:
                asyncio.run(download_video(1))
        finally:
            settings.set_db_override("access_token", None)

        assert exc.value.status_code == 502
        # Не `upstream_unavailable`: сайт ответил, просто ссылку отдавать нельзя.
        assert exc.value.detail["code"] == "unsafe_link"
        assert self.TOKEN not in str(exc.value.detail)

    def test_foreign_host_in_the_location_is_refused(self, monkeypatch, _embed) -> None:
        """Подменённый ответ не должен уводить человека на чужой сервер."""
        from app.api.stream import download_video
        from app.core.errors import ApiError

        self._respond(monkeypatch, 302, {"location": "https://evil.example.com/payload.exe"})
        with pytest.raises(ApiError) as exc:
            asyncio.run(download_video(1))

        assert exc.value.status_code == 502

    def test_foreign_host_is_named_in_the_error(self, monkeypatch, _embed) -> None:
        """Смену CDN на сайте чинят настройкой, но для этого нужно знать хост.

        Раньше отказ приходил как `upstream_unavailable` без имени — и интерфейс
        советовал сменить зеркало, что тут не помогает. Ссылка «скачать себе»
        открывается браузером напрямую, поэтому подсказка нужна в `message`.
        """
        from app.api.stream import download_video
        from app.core import errors
        from app.core.errors import ApiError

        self._respond(monkeypatch, 302, {"location": "https://bcdn.new-cdn.example/route/?m=1"})
        with pytest.raises(ApiError) as exc:
            asyncio.run(download_video(1))

        detail = exc.value.detail
        assert detail["code"] == errors.UNKNOWN_HOST
        assert detail["params"] == {"host": "bcdn.new-cdn.example"}
        assert "A365_EXTRA_CDN_HOSTS" in detail["message"]

    def test_foreign_first_hop_is_named_too(self, monkeypatch, _embed) -> None:
        """Ссылка из embed вне списка — тот же `unknown_host` с хостом, а не безымянный 404.

        Иначе переезд, который начинается с первой ссылки, а не с редиректа,
        снова выглядел бы как «ссылка недоступна» без единой зацепки.
        """
        from app.api.stream import download_video
        from app.core import errors
        from app.core.config import settings
        from app.core.errors import ApiError

        visited: list[str] = []

        class FakeHttp:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, **kwargs):
                visited.append(url)
                return httpx.Response(500, request=httpx.Request("GET", url))

        monkeypatch.setitem(settings._env, "mirrors", ("https://anime365.ru",))
        monkeypatch.setattr("app.api.stream.httpx.AsyncClient", lambda **kw: FakeHttp())
        with pytest.raises(ApiError) as exc:
            asyncio.run(download_video(1))

        detail = exc.value.detail
        assert detail["code"] == errors.UNKNOWN_HOST
        assert detail["params"] == {"host": "smotret-anime.app"}
        assert self.TOKEN not in str(detail)
        assert visited == [], "по ссылке с токеном не должно уйти ни одного запроса"

    @pytest.mark.parametrize(
        "location",
        [
            # Для `urlsplit` хост оканчивается на `.b-cdn.net`, а браузер читает
            # `\` как `/` и уходит на evil.com: отдать такой редирект — открытый
            # редирект с нашего домена.
            "https://evil.com\\x.b-cdn.net/payload",
            "https://user@moon-q1.b-cdn.net/route/?m=1",
            "http://192.168.1.1/",
            # IPv4 в записи, которую понимают браузер и glibc: это 127.0.0.1.
            "http://0x7f.1/",
            # Хост «свой», но браузер получил бы редирект вовсе не на файл.
            "javascript://moon-q1.b-cdn.net/%0aalert(1)",
        ],
    )
    def test_suspicious_location_is_not_advertised_as_new_cdn(
        self, monkeypatch, _embed, location: str
    ) -> None:
        """Подсказка «допишите хост в настройку» здесь не поможет — её и нет."""
        from app.api.stream import download_video
        from app.core import errors
        from app.core.errors import ApiError

        self._respond(monkeypatch, 302, {"location": location})
        with pytest.raises(ApiError) as exc:
            asyncio.run(download_video(1))

        assert exc.value.detail["code"] == errors.UNSAFE_LINK
        assert "A365_EXTRA_CDN_HOSTS" not in exc.value.detail["message"]

    def _player_fails_with(self, monkeypatch, error: Exception):
        from app.api.stream import stream_video
        from app.core.errors import ApiError

        async def refuse(*args, **kwargs):
            raise error

        monkeypatch.setattr("app.api.stream.open_checked", refuse)
        with pytest.raises(ApiError) as exc:
            asyncio.run(stream_video(1))
        return exc.value.detail

    def test_player_names_the_unknown_host(self, monkeypatch, _embed) -> None:
        """Без `host` словарь интерфейса показал бы буквальное `{host}`."""
        from app.services.downloader import UnknownHostError

        detail = self._player_fails_with(
            monkeypatch, UnknownHostError("https://bcdn.new-cdn.example/route/?m=1", "unknown")
        )

        assert detail["code"] == "unknown_host"
        assert detail["params"] == {"host": "bcdn.new-cdn.example"}

    def test_player_does_not_call_a_broken_chain_an_unknown_host(self, monkeypatch, _embed) -> None:
        from app.services.anime365 import Anime365Error

        detail = self._player_fails_with(
            monkeypatch, Anime365Error("Слишком длинная цепочка редиректов от moon-q1.b-cdn.net")
        )

        assert detail["code"] == "stream_open_failed"
        assert "moon-q1.b-cdn.net" in detail["params"]["reason"]

    @staticmethod
    def _player_gets(monkeypatch, content_type: str):
        from app.api.stream import stream_video

        async def answer(client, url, headers, **kwargs):
            return httpx.Response(
                200, headers={"content-type": content_type}, content=b"x",
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr("app.api.stream.open_checked", answer)

        async def run():
            response = await stream_video(1)
            # Тело дочитываем в том же цикле: его генератор и закрывает
            # клиент httpx, иначе тот остался бы открытым после теста.
            async for _ in response.body_iterator:
                pass
            return response

        return asyncio.run(run())

    def test_player_does_not_pass_an_html_page_for_video(self, monkeypatch, _embed) -> None:
        """Страница вместо видео — отказ с причиной, а не «браузер не справился»."""
        from app.core.errors import ApiError

        with pytest.raises(ApiError) as exc:
            self._player_gets(monkeypatch, "text/html; charset=utf-8")

        assert exc.value.detail["code"] == "stream_not_video"

    def test_player_passes_video(self, monkeypatch, _embed) -> None:
        response = self._player_gets(monkeypatch, "video/mp4")

        assert response.status_code == 200

    def test_host_from_the_setting_is_let_through(self, monkeypatch, _embed) -> None:
        """Тот же хост, дописанный в `extra_cdn_hosts`, отдаётся без релиза."""
        from app.api.stream import download_video
        from app.core.config import settings

        location = "https://bcdn.new-cdn.example/route/?m=1"
        monkeypatch.setitem(settings._env, "extra_cdn_hosts", ("new-cdn.example",))
        self._respond(monkeypatch, 302, {"location": location})
        response = asyncio.run(download_video(1))

        assert response.headers["location"] == location

    def test_answer_without_redirect_is_refused(self, monkeypatch, _embed) -> None:
        """Без редиректа отдавать нечего: сама ссылка несёт токен.

        Так отвечает сайт, когда подписка неактивна: 200 и HTML-страница вместо
        файла. Отдать её браузеру значило бы показать токен в адресной строке.
        """
        from app.api.stream import download_video
        from app.core.errors import ApiError

        self._respond(monkeypatch, 200, {"content-type": "text/html"})
        with pytest.raises(ApiError) as exc:
            asyncio.run(download_video(1))

        assert exc.value.status_code == 502


class TestPermissions:
    """Посмотреть и унести файл себе — разные права.

    Раньше и то и другое закрывалось правом «просмотр», и роль «пусть смотрит»
    молча включала в себя «пусть выносит копии наружу». Решение пользователя:
    развести, потому что последствия разные — просмотр ничего не оставляет,
    а скачивание отдаёт файл с сервера навсегда.

    Сложность в том, что маршрут у обоих действий **один и тот же**: плееру и
    кнопке «сохранить» нужен один файл, отличается только `download` в запросе.
    Поэтому право выбирается по параметру, и проверять надо поведение, а не
    объявления: по объявлению видно лишь, что зависимость стоит.
    """

    @staticmethod
    def _as(*permissions: str):
        """Клиент от имени пользователя ровно с этими правами."""
        from app.core.permissions import CurrentUser, current_user
        from app.main import app

        app.dependency_overrides[current_user] = lambda: CurrentUser(
            98, "гость", is_admin=False, permissions=list(permissions)
        )

    @staticmethod
    def _reset() -> None:
        from app.core.permissions import current_user
        from app.main import app

        app.dependency_overrides.pop(current_user, None)

    def test_watcher_cannot_save_the_file(self, client) -> None:
        """Тот самый случай, ради которого право и разведено."""
        from app.core.permissions import Permission

        self._as(Permission.WATCH.value)
        try:
            response = client.get("/api/stream/local/1?download=1")
        finally:
            self._reset()

        assert response.status_code == 403
        assert response.json()["detail"]["params"]["permission"] == Permission.DEVICE_DOWNLOADS.value

    def test_saver_cannot_stream(self, client) -> None:
        """И обратно: право уносить себе не даёт смотреть через наш прокси."""
        from app.core.permissions import Permission

        self._as(Permission.DEVICE_DOWNLOADS.value)
        try:
            response = client.get("/api/stream/local/1")
        finally:
            self._reset()

        assert response.status_code == 403
        assert response.json()["detail"]["params"]["permission"] == Permission.WATCH.value

    def test_saver_gets_past_the_permission_check(self, client) -> None:
        """С нужным правом проверка пропускает дальше — до самого файла.

        Файла с таким номером нет, поэтому ждём 404, а не 200: важно, что
        отказ пришёл уже не от прав.
        """
        from app.core.permissions import Permission

        self._as(Permission.DEVICE_DOWNLOADS.value)
        try:
            response = client.get("/api/stream/local/999999?download=1")
        finally:
            self._reset()

        assert response.status_code != 403

    def test_direct_download_route_needs_the_save_permission(self, client) -> None:
        """У ссылки на скачивание видео другого назначения нет."""
        from app.core.permissions import Permission

        self._as(Permission.WATCH.value)
        try:
            response = client.get("/api/stream/1/download")
        finally:
            self._reset()

        assert response.status_code == 403
        assert response.json()["detail"]["params"]["permission"] == Permission.DEVICE_DOWNLOADS.value

    def test_every_stream_route_still_asks_for_something(self) -> None:
        """Роутер больше не закрыт целиком — ни один маршрут не должен выпасть."""
        from app.main import app

        for route in app.routes:
            if not getattr(route, "path", "").startswith("/api/stream/"):
                continue
            names = [getattr(d.call, "__qualname__", "") for d in route.dependant.dependencies]
            assert any(
                n.startswith(("require.", "require_save_or_watch.")) for n in names
            ), f"{route.path} без проверки права: {names}"
