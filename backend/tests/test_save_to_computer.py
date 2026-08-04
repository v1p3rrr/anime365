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

    def test_foreign_host_in_the_location_is_refused(self, monkeypatch, _embed) -> None:
        """Подменённый ответ не должен уводить человека на чужой сервер."""
        from app.api.stream import download_video
        from app.core.errors import ApiError

        self._respond(monkeypatch, 302, {"location": "https://evil.example.com/payload.exe"})
        with pytest.raises(ApiError) as exc:
            asyncio.run(download_video(1))

        assert exc.value.status_code == 502

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
    """Скачивание на компьютер — то же право, что и просмотр."""

    def test_download_routes_require_watch(self) -> None:
        from app.main import app

        paths = {
            "/api/stream/{translation_id}/download",
            "/api/stream/local/{file_id}",
            "/api/stream/audio/{task_id}",
        }
        for route in app.routes:
            if getattr(route, "path", None) in paths:
                names = [
                    getattr(d.call, "__qualname__", "") for d in route.dependant.dependencies
                ]
                assert any(
                    n.startswith("require.") for n in names
                ), f"{route.path} без проверки права: {names}"
