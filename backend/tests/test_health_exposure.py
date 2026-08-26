"""`/api/health` не рассказывает лишнего тому, кто не вошёл.

Это единственный открытый эндпоинт, который что-то сообщает о самой установке,
и открыт он по необходимости: healthcheck докера ходит без куки. Но версия на
неавторизованном адресе позволяет прицельно подобрать эксплойт под конкретный
релиз, а `password_set` подсказывает, стоит ли вообще пытаться. Из интернета
у пользователя это закрыто взаимным TLS, а вот вся локальная сеть читала
подробности без единого вопроса.

Проверке живости ничего из этого не нужно — ей нужен код 200.
"""

from __future__ import annotations

#: Что не должно доезжать до неавторизованного.
SENSITIVE_FIELDS = ("version", "token_configured", "password_set", "base_url")


class TestWithoutPassword:
    """Пароль не задан — прятать не от кого, интерфейс и так открыт."""

    def test_details_are_shown(self, client) -> None:
        payload = client.get("/api/health").json()

        assert payload["version"]
        assert payload["status"] == "ok"


class TestPasswordSetButNotLoggedIn:
    def test_still_answers_two_hundred(self, client, with_password) -> None:
        """Иначе докер посчитает контейнер больным и уйдёт в перезапуск."""
        response = client.get("/api/health")

        assert response.status_code == 200

    def test_liveness_is_still_answered(self, client, with_password) -> None:
        payload = client.get("/api/health").json()

        assert payload["status"] == "ok"
        assert payload["db"] is True

    def test_no_details_leak(self, client, with_password) -> None:
        payload = client.get("/api/health").json()

        leaked = [field for field in SENSITIVE_FIELDS if field in payload]
        assert leaked == [], f"неавторизованному видно лишнее: {leaked}"


class TestAfterLogin:
    def test_details_come_back(self, client, with_password) -> None:
        client.post(
            "/api/auth/login",
            json={"login": with_password["login"], "password": with_password["password"]},
            headers={"Sec-Fetch-Site": "same-origin"},
        )

        payload = client.get("/api/health").json()

        for field in SENSITIVE_FIELDS:
            assert field in payload, f"вошедший должен видеть {field}"
