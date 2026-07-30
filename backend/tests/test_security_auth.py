"""Негативные тесты защитного слоя.

Каждый случай отвечает на конкретный вопрос «а что, если пользователь или
злоумышленник сделает так». Проверяем именно отказы: положительные сценарии
видно в работе приложения, а вот дыра тихо не проявится никогда.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.paths import UnsafePathError, resolve_within
from app.core.security import hash_password, sessions, verify_password
from app.core.settings_registry import get

# Тестовая БД в отдельном файле: боевую трогать нельзя, а bootstrap создаёт таблицы.
_tmp_db = Path(tempfile.gettempdir()) / "a365-tests.sqlite3"
os.environ.setdefault("A365_DB_PATH", str(_tmp_db))


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    settings.set_db_override("ui_password", None)
    sessions.drop_all()


@pytest.fixture
def with_password():
    """Включает пароль на время теста, не трогая БД."""
    settings.set_db_override("ui_password", hash_password("правильный-пароль"))
    yield
    settings.set_db_override("ui_password", None)
    sessions.drop_all()


class TestPasswordStorage:
    def test_ui_password_is_stored_as_hash(self) -> None:
        # Хеш не должен содержать сам пароль ни в каком виде.
        hashed = hash_password("s3cret")
        assert "s3cret" not in hashed
        assert hashed.startswith("$argon2")

    def test_plain_env_password_still_works(self) -> None:
        # A365_UI_PASSWORD в docker-compose задаётся открытым текстом —
        # заставлять пользователя хешировать руками было бы издевательством.
        settings.set_db_override("ui_password", "открытый")
        try:
            assert verify_password("открытый")
            assert not verify_password("другой")
        finally:
            settings.set_db_override("ui_password", None)

    def test_hashed_password_verifies(self) -> None:
        settings.set_db_override("ui_password", hash_password("хеш"))
        try:
            assert verify_password("хеш")
            assert not verify_password("хеше")
        finally:
            settings.set_db_override("ui_password", None)

    def test_empty_password_means_open_access(self) -> None:
        settings.set_db_override("ui_password", None)
        assert verify_password("что угодно")


class TestAuthGate:
    def test_api_is_closed_without_session(self, client, with_password) -> None:
        assert client.get("/api/settings").status_code == 401

    def test_api_is_open_when_no_password_set(self, client) -> None:
        # Пароль не задан — сервис работает без входа, но UI об этом предупреждает.
        assert client.get("/api/settings").status_code == 200

    def test_health_stays_public(self, client, with_password) -> None:
        # Иначе healthcheck докера начнёт считать контейнер мёртвым.
        assert client.get("/api/health").status_code in (200, 503)

    def test_login_opens_access(self, client, with_password) -> None:
        assert client.post("/api/auth/login", json={"password": "правильный-пароль"}).status_code == 200
        assert client.get("/api/settings").status_code == 200

    def test_wrong_password_rejected(self, client, with_password) -> None:
        response = client.post("/api/auth/login", json={"password": "неверный"})
        assert response.status_code == 401
        assert client.get("/api/settings").status_code == 401

    def test_logout_closes_access(self, client, with_password) -> None:
        client.post("/api/auth/login", json={"password": "правильный-пароль"})
        client.post("/api/auth/logout")
        assert client.get("/api/settings").status_code == 401

    def test_forged_cookie_rejected(self, client, with_password) -> None:
        client.cookies.set("a365_session", "a" * 43)
        assert client.get("/api/settings").status_code == 401


class TestCsrf:
    def test_cross_site_post_rejected(self, client) -> None:
        response = client.post(
            "/api/catalog/cards",
            json={"ids": [1]},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert response.status_code == 403

    def test_foreign_origin_rejected(self, client) -> None:
        response = client.post(
            "/api/catalog/cards",
            json={"ids": [1]},
            headers={"Origin": "https://evil.example.com"},
        )
        assert response.status_code == 403

    def test_same_origin_post_allowed(self, client) -> None:
        response = client.post(
            "/api/catalog/cards",
            json={"ids": []},
            headers={"Sec-Fetch-Site": "same-origin"},
        )
        assert response.status_code == 200

    def test_get_is_not_blocked(self, client) -> None:
        # Мутаций по GET в API нет намеренно, поэтому чтение можно не ограничивать.
        response = client.get("/api/catalog/genres", headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 200


class TestSecurityHeaders:
    def test_csp_present_and_without_unsafe_scripts(self, client) -> None:
        csp = client.get("/api/health").headers["content-security-policy"]
        assert "script-src 'self'" in csp
        # Именно этого сочетания быть не должно: с ним CSP не защищает от XSS.
        assert "script-src 'self' 'unsafe-inline'" not in csp
        assert "frame-ancestors 'none'" in csp

    def test_nosniff_and_referrer(self, client) -> None:
        headers = client.get("/api/health").headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "same-origin"

    def test_headers_are_set_on_denials_too(self, client, with_password) -> None:
        # Ответ 401 — тоже ответ, и он тоже рендерится браузером.
        assert "content-security-policy" in client.get("/api/settings").headers


class TestSecretsNeverLeave:
    def test_token_is_masked_in_settings_response(self, client) -> None:
        settings.set_db_override("access_token", "0" * 32 + "deadbeef")
        try:
            items = client.get("/api/settings").json()["groups"]
            flat = {s["key"]: s for group in items for s in group["settings"]}
            assert "deadbeef" not in str(flat["access_token"]["value"])
            assert flat["access_token"]["is_set"] is True
        finally:
            settings.set_db_override("access_token", None)

    def test_password_hash_is_masked_in_settings_response(self, client) -> None:
        settings.set_db_override("ui_password", hash_password("тайна"))
        try:
            # С заданным паролем настройки уже за входом, поэтому логинимся.
            client.post("/api/auth/login", json={"password": "тайна"})
            items = client.get("/api/settings").json()["groups"]
            flat = {s["key"]: s for group in items for s in group["settings"]}
            # Хеш наружу тоже не отдаём: по нему можно перебирать пароль офлайн.
            assert "$argon2" not in str(flat["ui_password"]["value"])
            assert flat["ui_password"]["is_set"] is True
        finally:
            settings.set_db_override("ui_password", None)
            sessions.drop_all()


class TestServerOnlySettings:
    def test_mirrors_marked_server_only(self) -> None:
        assert get("mirrors").server_only is True
        assert get("base_url").server_only is False

    def test_mirror_list_cannot_be_extended_from_ui(self, client) -> None:
        # Без этого allowlist зеркал терял бы смысл: достаточно было бы дописать
        # свой хост через UI, и проверка base_url начала бы его пропускать.
        response = client.put(
            "/api/settings",
            json={"updates": [{"key": "mirrors", "value": "https://evil.example.com"}]},
        )
        assert response.status_code == 400
        assert settings.get_list("mirrors") == get("mirrors").default

    def test_unknown_setting_rejected(self, client) -> None:
        response = client.put(
            "/api/settings", json={"updates": [{"key": "rm_rf", "value": "1"}]}
        )
        assert response.status_code == 400


class TestPathTraversal:
    def test_parent_escape_rejected(self) -> None:
        with pytest.raises(UnsafePathError):
            resolve_within("/media/anime", "../../etc/passwd")

    def test_absolute_outside_root_rejected(self) -> None:
        with pytest.raises(UnsafePathError):
            resolve_within("/media/anime", "/etc/passwd")

    def test_sneaky_middle_escape_rejected(self) -> None:
        with pytest.raises(UnsafePathError):
            resolve_within("/media/anime", "Show/../../../root/.ssh/authorized_keys")

    def test_normal_path_allowed(self) -> None:
        result = resolve_within("/media/anime", "Frieren/Season 01/ep.mkv")
        assert result.name == "ep.mkv"

    def test_root_itself_allowed(self) -> None:
        assert resolve_within("/media/anime", ".").name == "anime"


class TestRateLimit:
    def test_expensive_endpoint_eventually_refuses(self, client) -> None:
        from app.core.security import rate_limiter

        # Лимит на онлайн-поиск — 30 запросов в минуту на адрес.
        assert rate_limiter.allow("t:1", limit=2, window=60)
        assert rate_limiter.allow("t:1", limit=2, window=60)
        assert not rate_limiter.allow("t:1", limit=2, window=60)

    def test_limits_are_per_client(self) -> None:
        from app.core.security import rate_limiter

        assert rate_limiter.allow("t2:a", limit=1, window=60)
        assert not rate_limiter.allow("t2:a", limit=1, window=60)
        # Второй клиент не должен страдать из-за первого.
        assert rate_limiter.allow("t2:b", limit=1, window=60)


class TestLoginBruteForce:
    def test_attempts_are_limited(self) -> None:
        from app.core.security import AttemptLimiter

        limiter = AttemptLimiter(limit=3, window=60)
        for _ in range(3):
            assert limiter.check("1.2.3.4") == 0
            limiter.register_failure("1.2.3.4")
        assert limiter.check("1.2.3.4") > 0

    def test_success_resets_counter(self) -> None:
        from app.core.security import AttemptLimiter

        limiter = AttemptLimiter(limit=2, window=60)
        limiter.register_failure("1.2.3.4")
        limiter.register_failure("1.2.3.4")
        assert limiter.check("1.2.3.4") > 0
        limiter.reset("1.2.3.4")
        assert limiter.check("1.2.3.4") == 0


class TestFreeSpaceCheck:
    """Проверка места должна смотреть на ОБА тома, а не только на временный.

    Файл скачивается во временную папку и переезжает в библиотеку. В compose это
    разные монтирования, поэтому проверка только временной папки обнаруживала бы
    нехватку места уже после того, как сотни мегабайт скачаны.
    """

    def test_missing_directory_does_not_look_like_full_disk(self, tmp_path) -> None:
        from app.services.downloader import free_space_bytes

        # Первый запуск: /data/tmp ещё не создан. Раньше OSError давал 0 байт,
        # то есть «диск переполнен», и ни одна загрузка не стартовала.
        missing = tmp_path / "нет" / "такой" / "папки"
        assert free_space_bytes(missing) > 0

    def test_passes_when_both_volumes_have_room(self, tmp_path) -> None:
        from app.services.downloader import check_free_space

        assert check_free_space(tmp_path, tmp_path, min_free_bytes=1) is None

    def test_names_the_volume_that_is_short(self, tmp_path) -> None:
        from app.services.downloader import check_free_space

        # Заведомо недостижимый порог: сообщение обязано указать конкретный путь,
        # иначе пользователю негде искать причину.
        problem = check_free_space(tmp_path, tmp_path, min_free_bytes=1 << 60)
        assert problem is not None
        assert str(tmp_path) in problem

    def test_same_volume_is_checked_once(self, tmp_path) -> None:
        from app.services import downloader

        seen: list[str] = []
        original = downloader.free_space_bytes

        def spy(path):
            seen.append(str(path))
            return original(path)

        downloader.free_space_bytes = spy
        try:
            downloader.check_free_space(tmp_path, tmp_path, min_free_bytes=1)
        finally:
            downloader.free_space_bytes = original

        # Оба пути на одном томе — требовать место дважды неправильно.
        assert len(seen) == 1


class TestSettingsHelpIsNotStale:
    """В справке не должно быть чисел, которые устаревают вместе с данными."""

    def test_no_hardcoded_catalog_size(self) -> None:
        from app.core import settings_registry as reg

        # Каталог растёт, «29 231 тайтл» и «306 онгоингов» верны один день.
        # Фактические замеры отдаются отдельным полем hint из истории джоб.
        stale = ("29 231", "29 тысяч", "306 ", "44 запроса", "~32 секунды")
        for setting in reg.SETTINGS:
            for marker in stale:
                assert marker not in setting.help_ru, f"{setting.key}: устаревающее число {marker!r}"
