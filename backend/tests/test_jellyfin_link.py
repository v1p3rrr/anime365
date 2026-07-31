"""Ссылка на страницу тайтла в Jellyfin.

Проверяется в первую очередь сопоставление по хвосту пути: это единственное
место, где мы соединяем свою библиотеку с чужой, и ошибка здесь уводит
пользователя на страницу не того тайтла — молча и правдоподобно.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.services.jellyfin import JellyfinClient, _auth_headers, _normalize, usable_key

SERVER_ID = "0123456789abcdef0123456789abcdef"


def run(coro):
    """Асинхронных тестов в проекте нет, плагина тоже — гоняем через asyncio.run,
    как это уже сделано в тестах фоновых джоб."""
    return asyncio.run(coro)


def _items(*paths: tuple[str, str, str]) -> dict:
    """Ответ /Items из троек (id, тип, путь)."""
    return {"Items": [{"Id": i, "Type": t, "Path": p} for i, t, p in paths]}


class FakeClient(JellyfinClient):
    """Подменяет только сетевой слой — вся логика сопоставления настоящая."""

    def __init__(self, items: dict, server_id: str | None = SERVER_ID) -> None:
        super().__init__()
        self._items = items
        self._fake_server_id = server_id
        self.calls: list[str] = []

    async def _get(self, path, params=None):
        self.calls.append(path)
        if path == "/System/Info/Public":
            return {"Id": self._fake_server_id} if self._fake_server_id else {}
        if path == "/Items":
            return self._items
        return None

    @property
    def enabled(self) -> bool:
        return True

    @staticmethod
    def public_base() -> str:
        return "https://jellyfin.example.com"


@pytest.fixture
def library():
    return _items(
        ("id-yuru", "Series", "/media/anime/YuruYuri - Happy Go Lily"),
        ("id-yuru-s2", "Season", "/media/anime/YuruYuri - Happy Go Lily/Season 2"),
        ("id-zombie", "Series", "/media/anime/Zombie Land Saga"),
        ("id-movie", "Movie", "/media/movies/Your Name (2016)"),
    )


class TestPathMatching:
    def test_matches_by_tail_across_different_mount_points(self, library):
        # Суть подхода: мы видим папку относительно своего корня, Jellyfin —
        # относительно своего. Совпадать должны хвосты, а не полные пути.
        client = FakeClient(library)
        found = run(client.find_item(["YuruYuri - Happy Go Lily"]))
        assert found is not None
        assert found["id"] == "id-yuru"
        assert found["type"] == "Series"

    def test_season_folder_leads_to_season_page(self, library):
        # У нас сезон — отдельный тайтл со своей папкой. Вести на страницу
        # сериала целиком было бы менее точно, чем на страницу сезона.
        client = FakeClient(library)
        found = run(client.find_item(["YuruYuri - Happy Go Lily/Season 2"]))
        assert found["id"] == "id-yuru-s2"

    def test_similar_prefix_is_not_a_match(self):
        # «Saga» не должна совпасть с «Zombie Land Saga»: сравнение идёт по
        # границе компонента пути, а не по строковому вхождению.
        client = FakeClient(_items(("id-zombie", "Series", "/media/anime/Zombie Land Saga")))
        assert run(client.find_item(["Saga"])) is None
        assert run(client.find_item(["and Saga"])) is None

    def test_case_difference_does_not_break_the_match(self, library):
        client = FakeClient(library)
        found = run(client.find_item(["yuruyuri - HAPPY go lily"]))
        assert found["id"] == "id-yuru"

    def test_trailing_slash_and_backslashes_normalised(self, library):
        client = FakeClient(library)
        assert (run(client.find_item(["YuruYuri - Happy Go Lily/"])))["id"] == "id-yuru"
        assert (
            run(client.find_item(["YuruYuri - Happy Go Lily\\Season 2"]))
        )["id"] == "id-yuru-s2"

    def test_first_path_wins(self, library):
        # Основная привязка идёт первой и должна побеждать побочную.
        client = FakeClient(library)
        found = run(client.find_item(["Zombie Land Saga", "YuruYuri - Happy Go Lily"]))
        assert found["id"] == "id-zombie"

    def test_longest_hit_wins_within_one_path(self):
        # Если один и тот же хвост совпал с несколькими элементами, точнее тот,
        # чей путь длиннее: короткий мог совпасть по общему родителю.
        client = FakeClient(
            _items(
                ("outer", "Series", "/media/a/Show"),
                ("inner", "Season", "/media/a/Other/Show"),
            )
        )
        found = run(client.find_item(["Show"]))
        assert found["id"] == "inner"

    def test_unknown_title_gives_nothing(self, library):
        client = FakeClient(library)
        assert run(client.find_item(["Something Else Entirely"])) is None

    def test_items_without_path_are_skipped(self):
        # У виртуального сезона (метаданные есть, файлов нет) путь пустой —
        # сопоставлять нечем, и падать на этом нельзя.
        client = FakeClient({"Items": [{"Id": "x", "Type": "Season", "Path": None}]})
        assert run(client.find_item(["Whatever"])) is None


class TestLink:
    def test_url_shape_matches_the_web_client(self, library):
        # Формат сверен с бандлом веб-клиента Jellyfin:
        #   "#/details?id=" + id + "&serverId=" + serverId
        client = FakeClient(library)
        result = run(client.resolve_title(["Zombie Land Saga"]))
        assert result["url"] == (
            f"https://jellyfin.example.com/web/#/details?id=id-zombie&serverId={SERVER_ID}"
        )

    def test_no_server_id_means_no_link(self, library):
        # Без идентификатора сервера ссылка получилась бы битой — лучше не
        # показывать её вовсе, чем вести в никуда.
        client = FakeClient(library, server_id=None)
        assert run(client.resolve_title(["Zombie Land Saga"])) is None


class TestCaching:
    def test_library_is_fetched_once(self, library):
        client = FakeClient(library)
        run(client.find_item(["Zombie Land Saga"]))
        run(client.find_item(["YuruYuri - Happy Go Lily"]))
        assert client.calls.count("/Items") == 1

    def test_forget_makes_it_ask_again(self, library):
        client = FakeClient(library)
        run(client.find_item(["Zombie Land Saga"]))
        client.forget()
        run(client.find_item(["Zombie Land Saga"]))
        assert client.calls.count("/Items") == 2


class TestAddresses:
    def test_public_url_is_used_for_links(self):
        settings.set_db_override("jellyfin_url", "http://jellyfin:8096")
        settings.set_db_override("jellyfin_public_url", "https://jf.example.com")
        try:
            client = JellyfinClient()
            assert client.internal_base() == "http://jellyfin:8096"
            assert client.public_base() == "https://jf.example.com"
        finally:
            settings.set_db_override("jellyfin_url", None)
            settings.set_db_override("jellyfin_public_url", None)

    def test_public_falls_back_to_internal(self):
        # Обычный случай: Jellyfin не за обратным прокси, адрес один.
        settings.set_db_override("jellyfin_url", "http://192.168.1.10:8096")
        try:
            client = JellyfinClient()
            assert client.public_base() == "http://192.168.1.10:8096"
        finally:
            settings.set_db_override("jellyfin_url", None)

    def test_trailing_slash_does_not_double_up(self):
        settings.set_db_override("jellyfin_url", "http://jellyfin:8096/")
        try:
            assert JellyfinClient().public_base() == "http://jellyfin:8096"
        finally:
            settings.set_db_override("jellyfin_url", None)


class TestAuthHeader:
    def test_shape_matches_the_official_sdk(self):
        # Формат сверен с бандлом @jellyfin/sdk, отданным самим сервером.
        headers = _auth_headers("secret-key")
        auth = headers["Authorization"]
        assert auth.startswith("MediaBrowser ")
        assert 'Token="secret-key"' in auth
        assert 'Client="Anime365 Downloader"' in auth
        assert "DeviceId=" in auth

    def test_legacy_header_is_sent_too(self):
        # Старые сборки схемы MediaBrowser не знают; лишний заголовок дешевле
        # разбирательства «почему 401».
        assert _auth_headers("k")["X-Emby-Token"] == "k"

    def test_key_with_special_characters_is_escaped(self):
        # Кавычка в ключе иначе разорвала бы заголовок.
        auth = _auth_headers('a"b c')["Authorization"]
        assert '"' not in auth.split('Token="')[1][:-1]


def test_normalize():
    assert _normalize("/media/Anime/Show/") == "/media/anime/show"
    assert _normalize("C:\\media\\Show") == "c:/media/show"


class TestBadKey:
    """Ключ вставляют в форму мышью, и приезжает он не всегда чистым.

    Найдено проверкой на живом сервере: httpx на не-ASCII заголовке бросает
    UnicodeEncodeError, который НЕ наследник httpx.HTTPError — то есть летел
    мимо обработчика сетевых сбоев и давал 500 на странице тайтла.
    """

    def test_trailing_whitespace_is_trimmed(self):
        assert usable_key("  abc123\n") == "abc123"

    def test_non_ascii_key_is_rejected_not_crashed(self):
        assert usable_key("ключ-кириллицей") == ""

    def test_empty_stays_empty(self):
        assert usable_key("") == ""
        assert usable_key("   ") == ""

    def test_bad_key_does_not_break_the_lookup(self):
        # Главное следствие: страница тайтла обязана открыться, просто без ссылки.
        settings.set_db_override("enable_jellyfin", "true")
        settings.set_db_override("jellyfin_url", "http://127.0.0.1:1")
        settings.set_db_override("jellyfin_api_key", "ключ-кириллицей")
        try:
            assert run(JellyfinClient().find_item(["Whatever"])) is None
        finally:
            for key in ("enable_jellyfin", "jellyfin_url", "jellyfin_api_key"):
                settings.set_db_override(key, None)

    def test_refresh_names_the_reason(self):
        # Кнопка «проверить» в настройках должна сказать, что не так с ключом,
        # а не показать невнятный сбой соединения.
        settings.set_db_override("jellyfin_url", "http://127.0.0.1:1")
        settings.set_db_override("jellyfin_api_key", "ключ-кириллицей")
        try:
            result = run(JellyfinClient().refresh())
            assert result["ok"] is False
            assert "символ" in result["reason"]
        finally:
            for key in ("jellyfin_url", "jellyfin_api_key"):
                settings.set_db_override(key, None)
