"""Полный цикл входа: до, после, выход, снова.

Заведено по жалобе: вместо экрана входа человек увидел «Требуется вход» прямо
на странице каталога. Беда была во фронтенде — «данных о статусе ещё нет»
трактовалось как «вход выполнен», — но сторож нужен и здесь: если `/auth/status`
хоть раз соврёт о выходе, интерфейс снова покажет приложение, которое на каждый
запрос получает 401.
"""

from __future__ import annotations


def _status(client) -> dict:
    response = client.get("/api/auth/status")
    assert response.status_code == 200, response.text
    return response.json()


class TestAuthLifecycle:
    def test_status_tracks_the_whole_cycle(self, client, with_password) -> None:
        assert _status(client) == {"password_required": True, "authenticated": False}

        assert client.post("/api/auth/login", json=with_password).status_code == 200
        assert _status(client) == {"password_required": True, "authenticated": True}

        assert client.post("/api/auth/logout").status_code == 200
        assert _status(client) == {"password_required": True, "authenticated": False}

    def test_protected_endpoint_follows_the_status(self, client, with_password) -> None:
        """Статус и настоящие эндпоинты обязаны говорить одно и то же.

        ⚠️ Адрес здесь обязан быть **существующим**. Раньше стоял `/api/catalog`,
        которого нет: запрос уходил в SPA-заглушку, та отдавала `index.html`, и
        проверка «после входа 200» проходила, ничего не проверяя. На машине
        разработчика — годами, потому что там лежит собранная статика; в CI, где
        её нет, тот же запрос сразу дал 404.
        """
        endpoint = "/api/catalog/ids?limit=1"
        assert client.get(endpoint).status_code == 401

        client.post("/api/auth/login", json=with_password)
        assert client.get(endpoint).status_code == 200

        client.post("/api/auth/logout")
        assert client.get(endpoint).status_code == 401

    def test_status_stays_open_without_a_session(self, client, with_password) -> None:
        """Единственный эндпоинт до входа — иначе экран входа сам себя не покажет."""
        assert client.get("/api/auth/status").status_code == 200

    def test_wrong_password_does_not_open_a_session(self, client, with_password) -> None:
        response = client.post(
            "/api/auth/login",
            json={"login": with_password["login"], "password": "заведомо-неверный"},
        )

        assert response.status_code == 401
        assert _status(client)["authenticated"] is False
