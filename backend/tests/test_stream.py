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
