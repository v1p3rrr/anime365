"""API автоскачивания: настройки глобальные и по тайтлу.

Всё закрыто правом `downloads` — автоскачивание тратит диск и трафик сервера
ровно так же, как нажатая руками кнопка «скачать».

Отдельно проверяется нормализация вводимых имён. Человек вписывает группу
руками, как помнит, — «Wakanim [1080p]», «wakanim», «Wakanim». Если не привести
это к тому же виду, что и каталог, список приоритетов молча перестанет
совпадать хоть с чем-нибудь, и субтитры не скачаются без единой ошибки.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.db import session_scope
from app.models.autodownload import AutoDownloadRule, SeriesAutoDownload
from app.models.catalog import Series

SERIES = 950_001


@pytest.fixture(autouse=True)
def clean():
    def wipe() -> None:
        with session_scope() as session:
            session.execute(delete(AutoDownloadRule))
            session.execute(
                delete(SeriesAutoDownload).where(SeriesAutoDownload.series_id == SERIES)
            )

    wipe()
    with session_scope() as session:
        session.merge(Series(id=SERIES, title="API Test", type="tv"))
    yield
    wipe()


class TestGlobalRules:
    def test_defaults_are_off_for_every_language(self, client: TestClient) -> None:
        body = client.get("/api/autodownload").json()

        assert set(body["rules"]) == {"ru", "en", "ja"}
        assert all(rule["mode"] == "off" for rule in body["rules"].values())

    def test_saving_a_rule_returns_the_whole_picture(self, client: TestClient) -> None:
        body = client.put(
            "/api/autodownload/ru",
            json={"mode": "all", "priorities": ["Wakanim"]},
        ).json()

        assert body["rules"]["ru"]["mode"] == "all"
        assert body["rules"]["en"]["mode"] == "off", "соседний язык трогать нельзя"

    def test_names_are_normalized_on_the_way_in(self, client: TestClient) -> None:
        """Иначе введённое руками не совпадёт с тем, что пришло с сайта."""
        body = client.put(
            "/api/autodownload/ru",
            json={"mode": "all", "priorities": ["Wakanim [1080p]", "  Kazoku  "]},
        ).json()

        assert body["rules"]["ru"]["priorities"] == ["wakanim", "kazoku"]

    def test_duplicates_collapse_but_order_survives(self, client: TestClient) -> None:
        body = client.put(
            "/api/autodownload/ru",
            json={"mode": "all", "priorities": ["Kazoku", "Wakanim", "kazoku"]},
        ).json()

        assert body["rules"]["ru"]["priorities"] == ["kazoku", "wakanim"]

    def test_an_unknown_language_is_refused(self, client: TestClient) -> None:
        assert client.put("/api/autodownload/de", json={"mode": "all"}).status_code == 400

    def test_an_unknown_mode_is_refused(self, client: TestClient) -> None:
        response = client.put("/api/autodownload/ru", json={"mode": "иногда"})

        assert response.status_code == 400


class TestSeriesSettings:
    def test_an_untouched_title_has_nothing_of_its_own(self, client: TestClient) -> None:
        body = client.get(f"/api/autodownload/series/{SERIES}").json()

        assert body["own"] == {}

    def test_effective_shows_why_nothing_happens(self, client: TestClient) -> None:
        """Режим тайтла «по умолчанию», а глобально выключено — качать нечего."""
        client.put("/api/autodownload/ru", json={"mode": "off", "priorities": ["Kazoku"]})

        body = client.get(f"/api/autodownload/series/{SERIES}").json()

        assert body["effective"] == {}

    def test_a_title_list_shows_up_as_its_own(self, client: TestClient) -> None:
        client.put("/api/autodownload/ru", json={"mode": "all", "priorities": ["Wakanim"]})
        client.put(
            f"/api/autodownload/series/{SERIES}/ru",
            json={"mode": "on", "priorities": ["Kazoku"]},
        )

        body = client.get(f"/api/autodownload/series/{SERIES}").json()

        assert body["effective"]["ru"]["priorities"] == ["kazoku"]
        assert body["effective"]["ru"]["from_series"] is True

    def test_clearing_returns_to_the_global_list(self, client: TestClient) -> None:
        """Ровно то, что просил пользователь: удалили свой — вернулся общий."""
        client.put("/api/autodownload/ru", json={"mode": "all", "priorities": ["Wakanim"]})
        client.put(
            f"/api/autodownload/series/{SERIES}/ru",
            json={"mode": "on", "priorities": ["Kazoku"]},
        )

        body = client.delete(f"/api/autodownload/series/{SERIES}/ru").json()

        assert body["own"] == {}
        assert body["effective"]["ru"]["priorities"] == ["wakanim"]
        assert body["effective"]["ru"]["from_series"] is False

    def test_a_missing_title_is_a_404(self, client: TestClient) -> None:
        response = client.put(
            "/api/autodownload/series/99999999/ru", json={"mode": "on"}
        )

        assert response.status_code == 404


class TestConfiguredList:
    def test_empty_until_something_is_set(self, client: TestClient) -> None:
        assert client.get("/api/autodownload/configured").json()["series"] == []

    def test_a_configured_title_shows_up_with_its_languages(self, client: TestClient) -> None:
        client.put(
            f"/api/autodownload/series/{SERIES}/ru",
            json={"mode": "on", "priorities": ["Kazoku"]},
        )

        rows = client.get("/api/autodownload/configured").json()["series"]

        assert [row["series_id"] for row in rows] == [SERIES]
        assert rows[0]["languages"]["ru"] == {"mode": "on", "has_own_list": True}

    def test_a_title_left_at_defaults_is_not_listed(self, client: TestClient) -> None:
        """Строка есть, но не решает ничего — показывать её незачем."""
        client.put(
            f"/api/autodownload/series/{SERIES}/ru", json={"mode": "default"}
        )

        assert client.get("/api/autodownload/configured").json()["series"] == []


class TestAuthors:
    def test_suggestions_answer_even_when_empty(self, client: TestClient) -> None:
        """У свежей установки каталог пуст — это нормальный ответ, не ошибка."""
        response = client.get("/api/autodownload/authors", params={"lang": "ja"})

        assert response.status_code == 200
        assert isinstance(response.json()["authors"], list)

    def test_an_unknown_language_is_refused(self, client: TestClient) -> None:
        response = client.get("/api/autodownload/authors", params={"lang": "de"})

        assert response.status_code == 400


class TestPermissions:
    def test_everything_here_needs_the_downloads_permission(self, client: TestClient) -> None:
        from app.core.permissions import CurrentUser, Permission, current_user
        from app.main import app

        app.dependency_overrides[current_user] = lambda: CurrentUser(
            97, "гость", is_admin=False, permissions=[Permission.WATCH.value]
        )
        try:
            for method, path in (
                ("get", "/api/autodownload"),
                ("get", "/api/autodownload/configured"),
                ("get", f"/api/autodownload/series/{SERIES}"),
                ("post", "/api/autodownload/run"),
            ):
                response = getattr(client, method)(path)
                assert response.status_code == 403, path
        finally:
            app.dependency_overrides.pop(current_user, None)
