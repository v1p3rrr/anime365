"""Проксирование потока для встроенного плеера.

Главное здесь — не «играет ли видео», а два свойства безопасности:
токен не уходит в браузер, и эндпоинт не превращается в открытый прокси.
"""

from __future__ import annotations

import pytest

from app.api.stream import _pick_stream_url
from app.services.downloader import is_allowed_target


def _stream(height: int, url: str) -> dict:
    return {"height": height, "urls": [url]}


class TestStreamUrlChoice:
    URLS = [
        _stream(1080, "https://ru-1.quantum-phantom-moon.ru/a"),
        _stream(720, "https://ru-1.quantum-phantom-moon.ru/b"),
        _stream(360, "https://ru-1.quantum-phantom-moon.ru/c"),
    ]

    def test_without_preference_takes_the_best(self) -> None:
        assert _pick_stream_url(self.URLS, None).endswith("/a")

    def test_exact_step_is_used(self) -> None:
        assert _pick_stream_url(self.URLS, 720).endswith("/b")

    def test_falls_back_down_not_up(self) -> None:
        # Запрошено 1080, но есть только 480 — отдаём 480, а не отказываем.
        limited = [_stream(480, "https://ru-1.quantum-phantom-moon.ru/d")]
        assert _pick_stream_url(limited, 1080).endswith("/d")

    def test_below_all_steps_gives_the_smallest(self) -> None:
        # Просили 240, а минимум 360: играть что-то лучше, чем ничего.
        assert _pick_stream_url(self.URLS, 240).endswith("/c")

    def test_empty_list_gives_none(self) -> None:
        assert _pick_stream_url([], 720) is None

    def test_entries_without_urls_are_skipped(self) -> None:
        broken = [{"height": 1080, "urls": []}, _stream(360, "https://x.b-cdn.net/ok")]
        assert _pick_stream_url(broken, 1080).endswith("/ok")


class TestProxyIsNotOpen:
    """Эндпоинт принимает id перевода, а не URL — но ссылку из API тоже проверяем.

    Подменённый ответ API не должен заставить сервер сходить на посторонний
    хост: иначе получился бы открытый прокси внутрь локальной сети.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example.com/video.mp4",
            "http://127.0.0.1:8080/api/settings",
            "http://192.168.1.1/",
            "https://quantum-phantom-moon.ru.evil.com/x",
            "https://evilmelon-soda.org/route/?m=1",
            "https://bcdn.melon-soda.org.evil.com/route/?m=1",
        ],
    )
    def test_foreign_hosts_rejected(self, url: str) -> None:
        assert is_allowed_target(url) is False

    @pytest.mark.parametrize(
        "url",
        [
            "https://ru-549959.quantum-phantom-moon.ru/s/q1/route/?m=1",
            "https://moon-q1561620.b-cdn.net/route/?m=1",
            # CDN с 2026-09-27: без него «скачать себе» и очередь отдавали 502.
            "https://bcdn.melon-soda.org/route/?m=28609221&r=60ec1f26",
            "https://smotret-anime.app/translations/mp4/1",
        ],
    )
    def test_known_hosts_allowed(self, url: str) -> None:
        assert is_allowed_target(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            # До двоеточия стоит «свой» CDN, но это логин: httpx идёт на 192.168.1.1.
            "http://x.b-cdn.net:80@192.168.1.1/",
            "https://a.b-cdn.net:x@evil.com/",
            "https://moon.b-cdn.net@127.0.0.1:8080/api/settings",
            # Логин на своём хосте CDN не выдаёт — такую ссылку тоже не берём.
            "https://user@moon-q1.b-cdn.net/route/?m=1",
        ],
    )
    def test_userinfo_does_not_disguise_the_host(self, url: str) -> None:
        assert is_allowed_target(url) is False

    @pytest.mark.parametrize(
        "url",
        [
            # `urlsplit` видит хост `evil.com\x.b-cdn.net`, браузер — `evil.com`.
            "https://evil.com\\x.b-cdn.net/",
            "https://evil.com\\.b-cdn.net/",
            "https://evil.com%5Cx.b-cdn.net/",
            # Битый адрес: `urlsplit` на нём падает, а не отвечает.
            "http://[moon-q1.b-cdn.net/",
            "http://[::1]/",
            # IPv4 в записи, которую браузер (WHATWG) и glibc читают как адрес:
            # `0x7f.1` в контейнере резолвится в 127.0.0.1.
            "http://0x7f.1/",
            "http://0x7f000001/",
            "http://127.1/",
            # Хост «свой», но схема не http: «скачать себе» отдал бы такой редирект.
            "javascript://moon-q1.b-cdn.net/%0aalert(1)",
            "ftp://moon-q1.b-cdn.net/a",
            # Без хоста и с пустыми метками — не имя, которое можно дописать в настройку.
            "https:///route/?m=1",
            "https://moon-q1.b-cdn.net./route/?m=1",
            "https://.b-cdn.net/",
            "https://a..b-cdn.net/",
        ],
    )
    def test_hosts_other_parsers_read_differently_are_rejected(self, url: str) -> None:
        from app.services.downloader import REJECT_SUSPICIOUS, classify_target

        assert classify_target(url) == REJECT_SUSPICIOUS

    @pytest.mark.parametrize(
        ("url", "reason"),
        [
            ("https://bcdn.melon-soda.org/route/?m=1", None),
            ("https://bcdn.new-cdn.example/route/?m=1", "unknown"),
            ("https://user@bcdn.melon-soda.org/route/?m=1", "suspicious"),
            ("http://192.168.1.1/", "suspicious"),
            ("http://0x7f.1/", "suspicious"),
        ],
    )
    def test_reason_tells_a_new_cdn_from_a_forged_link(self, url: str, reason) -> None:
        """Подсказка про настройку нужна только первому: второе ею не лечится."""
        from app.services.downloader import (
            UnknownHostError,
            classify_target,
            ensure_allowed_target,
        )

        assert classify_target(url) == reason
        if reason is None:
            ensure_allowed_target(url)
            return
        with pytest.raises(UnknownHostError) as exc:
            ensure_allowed_target(url)
        assert exc.value.fixable is (reason == "unknown")
        assert ("A365_EXTRA_CDN_HOSTS" in str(exc.value)) is exc.value.fixable

    def test_error_survives_copy_and_pickle(self) -> None:
        """Исключение пересобирается из `args`, а там только текст — без токена."""
        import copy
        import pickle

        from app.services.downloader import UnknownHostError

        error = UnknownHostError("https://bcdn.new-cdn.example/x?access_token=SECRET", "unknown")
        blob = pickle.dumps(error)

        assert b"SECRET" not in blob
        for clone in (copy.copy(error), pickle.loads(blob)):
            assert str(clone) == str(error)
            assert (clone.host, clone.reason, clone.fixable) == (
                "bcdn.new-cdn.example",
                "unknown",
                True,
            )

    def test_ip_mirror_still_works(self, monkeypatch) -> None:
        """Список зеркал допускает `https://10.0.0.5` — такое зеркало работало до D-214.

        Проверка на IP стоит после сверки с зеркалами: иначе она отрезала бы
        зеркало, которое администратор вписал сам.
        """
        from app.core.config import settings

        monkeypatch.setitem(settings._env, "mirrors", ("https://10.0.0.5",))

        assert is_allowed_target("https://10.0.0.5/translations/mp4/1") is True
        assert is_allowed_target("https://10.0.0.6/translations/mp4/1") is False

    def test_redirect_errors_carry_the_host_not_the_link(self) -> None:
        """В начале ссылки на скачивание стоит токен — в текст ошибки она не идёт."""
        import asyncio

        import httpx

        from app.services.anime365 import Anime365Error
        from app.services.downloader import open_checked

        link = "https://smotret-anime.app/translations/mp4/1?access_token=SECRET"

        def no_location(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, request=request)

        async def run() -> None:
            transport = httpx.MockTransport(no_location)
            async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
                await open_checked(client, link, {}, stream=False)

        with pytest.raises(Anime365Error) as exc:
            asyncio.run(run())
        assert "SECRET" not in str(exc.value)
        assert "smotret-anime.app" in str(exc.value)


class TestExtraCdnHosts:
    """`A365_EXTRA_CDN_HOSTS` — чтобы переезд сайта на новый CDN не ждал релиза."""

    @pytest.fixture
    def extra(self, monkeypatch):
        from app.core.config import settings

        def _set(*hosts: str) -> None:
            monkeypatch.setitem(settings._env, "extra_cdn_hosts", hosts)

        return _set

    def test_unknown_cdn_is_refused_by_default(self) -> None:
        assert is_allowed_target("https://bcdn.new-cdn.example/route/?m=1") is False

    def test_entry_lets_the_domain_and_its_subdomains_through(self, extra) -> None:
        extra("new-cdn.example")

        assert is_allowed_target("https://new-cdn.example/route/?m=1") is True
        assert is_allowed_target("https://bcdn.new-cdn.example/route/?m=1") is True

    def test_entry_is_not_a_bare_suffix(self, extra) -> None:
        """`new-cdn.example` не должен пускать `evilnew-cdn.example`."""
        extra("new-cdn.example")

        assert is_allowed_target("https://evilnew-cdn.example/x") is False
        assert is_allowed_target("https://new-cdn.example.evil.com/x") is False

    def test_leading_dot_and_case_do_not_matter(self, extra) -> None:
        extra(".New-CDN.example")

        assert is_allowed_target("https://bcdn.new-cdn.example/x") is True

    def test_builtin_list_still_works_with_the_setting(self, extra) -> None:
        """Дополняет, а не заменяет: иначе переопределение спрятало бы новый встроенный CDN."""
        extra("new-cdn.example")

        assert is_allowed_target("https://bcdn.melon-soda.org/route/?m=1") is True

    def test_duplicate_of_a_builtin_domain_changes_nothing(self, extra, caplog) -> None:
        """Дубль встроенного домена не ломает проверку, но подсказывает, что он лишний."""
        import logging

        from app.services.downloader import _allowed

        _allowed.cache_clear()
        extra("melon-soda.org", "bcdn.melon-soda.org", ".MELON-SODA.org")
        with caplog.at_level(logging.INFO, logger="app.services.downloader"):
            assert is_allowed_target("https://bcdn.melon-soda.org/route/?m=1") is True
            assert is_allowed_target("https://evilmelon-soda.org/x") is False
            # Второй вызов — из кэша: строка в логе одна на конфигурацию, а не на ссылку.
            assert is_allowed_target("https://bcdn.melon-soda.org/route/?m=2") is True

        notes = [r.getMessage() for r in caplog.records if "уже покрыт" in r.getMessage()]
        assert len(notes) == 3

    def test_entries_may_overlap_each_other(self, extra) -> None:
        extra("new-cdn.example", "bcdn.new-cdn.example", "new-cdn.example")

        assert is_allowed_target("https://bcdn.new-cdn.example/x") is True
        assert is_allowed_target("https://other.new-cdn.example/x") is True

    def test_entry_inside_a_builtin_does_not_narrow_it(self, extra) -> None:
        """Запись уже встроенного поддомена не сужает встроенный домен до себя."""
        extra("cdn.melon-soda.org")

        assert is_allowed_target("https://bcdn.melon-soda.org/x") is True
        assert is_allowed_target("https://a.cdn.melon-soda.org/x") is True

    def test_builtin_domains_are_matched_like_entries(self) -> None:
        """Встроенный список и настройка идут через один матчер — и апекс тоже."""
        assert is_allowed_target("https://melon-soda.org/route/?m=1") is True
        assert is_allowed_target("https://evilmelon-soda.org/route/?m=1") is False

    def test_messy_env_value_is_normalised(self, extra) -> None:
        """Пробелы, пустые элементы и хвостовая запятая из `.env` не мешают."""
        from app.core.settings_registry import get

        setting = get("extra_cdn_hosts")
        value = setting.validate(setting.parse(" new-cdn.example , ,.Other.example,"))
        extra(*value)

        assert value == ("new-cdn.example", ".Other.example")
        assert is_allowed_target("https://x.other.example/") is True

    def test_empty_env_value_means_not_set(self) -> None:
        """На сервере переменная стоит пустой — это «не задано», а не ошибка."""
        from app.core.settings_registry import get

        setting = get("extra_cdn_hosts")
        assert setting.validate(setting.parse("")) == ()

    def test_setting_is_server_only(self) -> None:
        from app.core.settings_registry import get

        assert get("extra_cdn_hosts").server_only is True

    @pytest.mark.parametrize(
        "raw",
        [
            "https://new-cdn.example",
            "new-cdn.example:443",
            "new-cdn.example/route",
            "org",
            "192.168.1.1",
            # Тоже IPv4: браузер и glibc читают `0x7f.1` как 127.0.0.1.
            "0x7f.1",
            "0x7f000001.0x1",
            "cdn.example.017",
            "*.new-cdn.example",
            "-.-",
            "new-.example",
            # Общий хостинг: поддомен в нём заводит кто угодно.
            "cloudfront.net",
            "windows.net",
            ".github.io",
        ],
    )
    def test_malformed_entries_are_rejected(self, raw: str) -> None:
        from app.core.settings_registry import ValidationError, get

        setting = get("extra_cdn_hosts")
        with pytest.raises(ValidationError):
            setting.validate(setting.parse(raw))

    def test_valid_list_is_accepted(self) -> None:
        from app.core.settings_registry import get

        setting = get("extra_cdn_hosts")
        value = setting.validate(
            setting.parse("new-cdn.example, .other.example, d123.cloudfront.net")
        )

        # Полное имя в общем хостинге — можно: оно одно, а не целая зона.
        assert value == ("new-cdn.example", ".other.example", "d123.cloudfront.net")


class TestSubtitlesNameTheHost:
    """Субтитры отказывают по хосту так же, как плеер и «скачать себе».

    До D-214 первая ссылка вне списка давала безымянный 404
    `subtitles_unavailable`, а редирект на чужой хост — 502 без имени хоста.
    """

    @pytest.fixture
    def subtitles_at(self, monkeypatch):
        from app.services.anime365 import EmbedInfo

        def _set(url: str | None) -> None:
            async def fake_get_embed(translation_id: int) -> dict:
                return {"has_subtitles": True}

            class FakeClient:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *exc):
                    return False

                async def embed(self, translation_id: int) -> EmbedInfo:
                    return EmbedInfo(
                        translation_id=translation_id,
                        download=[],
                        stream=[],
                        subtitles_url=url,
                        embed_url=None,
                    )

            monkeypatch.setattr("app.api.stream.get_embed", fake_get_embed)
            monkeypatch.setattr("app.api.stream.Anime365Client", FakeClient)

        return _set

    @staticmethod
    def _refusal() -> dict:
        import asyncio

        from app.api.stream import stream_subtitles
        from app.core.errors import ApiError

        with pytest.raises(ApiError) as exc:
            asyncio.run(stream_subtitles(1))
        return exc.value.detail

    def test_foreign_first_link_is_named(self, subtitles_at) -> None:
        subtitles_at("https://subs.new-cdn.example/1.ass")

        detail = self._refusal()

        assert detail["code"] == "unknown_host"
        assert detail["params"] == {"host": "subs.new-cdn.example"}

    def test_redirect_to_a_foreign_host_is_named(self, monkeypatch, subtitles_at) -> None:
        import httpx

        real_client = httpx.AsyncClient

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://bcdn.new-cdn.example/1.ass"})

        subtitles_at("https://smotret-anime.app/translations/ass/1")
        monkeypatch.setattr(
            "app.api.stream.httpx.AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        )

        detail = self._refusal()

        assert detail["code"] == "unknown_host"
        assert detail["params"] == {"host": "bcdn.new-cdn.example"}

    def test_suspicious_link_gets_no_setting_hint(self, subtitles_at) -> None:
        subtitles_at("http://0x7f.1/1.ass")

        detail = self._refusal()

        assert detail["code"] == "unsafe_link"
        assert "A365_EXTRA_CDN_HOSTS" not in detail["message"]

    def test_missing_link_is_still_a_404(self, subtitles_at) -> None:
        subtitles_at(None)

        assert self._refusal()["code"] == "subtitles_unavailable"


class TestStreamEndpointIsProtected:
    def test_requires_login_when_password_set(self, client, with_password) -> None:
        # Иначе премиум-подпиской пользовался бы кто угодно, у кого есть адрес.
        assert client.get("/api/stream/1/video").status_code == 401
        assert client.get("/api/stream/1/subtitles").status_code == 401

    def test_no_url_parameter_exists(self, client) -> None:
        from app.main import app

        # Открытых прокси вида /fetch?url=… в приложении быть не должно.
        for route in app.routes:
            path = getattr(route, "path", "")
            if "/stream/" in path:
                assert "{url" not in path


class TestDirectCdnRewrite:
    """Переписывание прокси-ссылки на прямой хост зоны BunnyCDN.

    Это эксперимент за выключенным тогглом: на тестовом файле прямой хост был
    быстрее в 5.6 раза, но на реальном видео замер воспроизвести не удалось —
    подписи одноразовые. Поэтому важно, чтобы переписывание было точным, а
    неподходящие ссылки не трогались вовсе.
    """

    def test_proxy_link_becomes_direct_zone(self) -> None:
        from app.services.downloader import direct_cdn_url

        result = direct_cdn_url(
            "https://ru-549959.quantum-phantom-moon.ru/s/q1561620/route/?m=1&r=abc"
        )
        # Префикс /s/{zone} обязан исчезнуть: с ним Bunny отвечает 404.
        assert result == "https://moon-q1561620.b-cdn.net/route/?m=1&r=abc"

    def test_query_with_signature_is_preserved(self) -> None:
        from app.services.downloader import direct_cdn_url

        result = direct_cdn_url(
            "https://ru-1.quantum-phantom-moon.ru/s/q42/a/b?m=xyz&r=1&extra=2"
        )
        # Подпись потерять нельзя — без неё CDN отдаст отказ.
        assert result.endswith("?m=xyz&r=1&extra=2")

    def test_result_stays_within_allowlist(self) -> None:
        from app.services.downloader import direct_cdn_url, is_allowed_target

        result = direct_cdn_url("https://ru-1.quantum-phantom-moon.ru/s/q42/route/?m=1")
        # Переписывание не должно уводить за пределы разрешённых хостов.
        assert is_allowed_target(result)

    def test_non_proxy_links_untouched(self) -> None:
        from app.services.downloader import direct_cdn_url

        # Зеркало сайта, а не CDN — переписывать нечего.
        assert direct_cdn_url("https://smotret-anime.app/translations/mp4/1") is None
        # Прокси-хост, но путь другой формы — гадать не будем.
        assert direct_cdn_url("https://ru-1.quantum-phantom-moon.ru/other/path") is None
        # Уже прямая ссылка.
        assert direct_cdn_url("https://moon-q1.b-cdn.net/route/?m=1") is None

    def test_lookalike_host_is_not_rewritten(self) -> None:
        from app.services.downloader import direct_cdn_url

        # Похожий, но чужой домен трогать нельзя.
        assert direct_cdn_url("https://quantum-phantom-moon.ru.evil.com/s/q1/x") is None
