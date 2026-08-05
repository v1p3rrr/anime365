"""Временный пароль: выдан администратором — годится только чтобы войти и сменить.

Просьба пользователя: при создании учётной записи можно поставить галочку
«временный пароль», и тогда владелец задаёт свой при первом входе. Отдельно —
сброс: администратор нажимает кнопку, сервер придумывает пароль и показывает
его один раз.

Главное здесь не форма, а **запрет**. Если бы флаг только рисовал экран смены
пароля, закрыть этот экран было бы достаточно, чтобы продолжить работать под
паролём, который знает не только владелец. Поэтому проверки прав спотыкаются о
флаг, и почти весь API отвечает отказом, пока пароль не сменён.

Три маршрута обязаны остаться доступными, иначе выйти из положения нечем:
`/api/users/me` (узнать, чего от тебя хотят), смена своего пароля (исполнить)
и выход.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import session_scope
from app.core.security import (
    MIN_PASSWORD_LENGTH,
    TEMP_PASSWORD_LENGTH,
    sessions,
    verify_user,
)
from app.models import AppUser

NAME = "временный"


def _row() -> AppUser | None:
    with session_scope() as session:
        row = session.execute(select(AppUser).where(AppUser.name == NAME)).scalar_one_or_none()
        if row is not None:
            session.expunge(row)
        return row


@pytest.fixture
def created(client: TestClient):
    """Учётная запись с временным паролём, заведённая штатным путём.

    Пароль здесь не задаётся: его придумывает сервер и возвращает один раз.
    """
    response = client.post("/api/users", json={"name": NAME, "temporary_password": True})
    assert response.status_code == 200, response.text
    body = response.json()
    user_id, password = body["id"], body["password"]

    yield user_id, password

    with session_scope() as session:
        row = session.get(AppUser, user_id)
        if row is not None:
            session.delete(row)
    sessions.drop_all()


def _acting_as(user_id: int):
    """Запросы идут от имени этой записи, как после входа."""
    from app.core.permissions import current_user
    from app.main import app

    def override():
        from app.core.permissions import CurrentUser

        with session_scope() as session:
            row = session.get(AppUser, user_id)
            return CurrentUser(
                row.id,
                row.name,
                row.is_admin,
                list(row.permissions or []),
                must_change_password=bool(row.must_change_password),
            )

    app.dependency_overrides[current_user] = override


def _stop_acting() -> None:
    from app.core.permissions import current_user
    from app.main import app

    app.dependency_overrides.pop(current_user, None)


class TestTheFlagIsSetOnlyWhenAsked:
    def test_checkbox_marks_the_password_temporary(self, created) -> None:
        assert _row().must_change_password is True

    def test_without_the_checkbox_the_password_is_permanent(self, client: TestClient) -> None:
        response = client.post(
            "/api/users",
            json={"name": "постоянный", "password": "обычный-пароль", "temporary_password": False},
        )
        try:
            assert response.json()["must_change_password"] is False
        finally:
            with session_scope() as session:
                row = session.get(AppUser, response.json()["id"])
                if row is not None:
                    session.delete(row)

    def test_no_password_at_all_means_no_flag(self, client: TestClient) -> None:
        """Запись без пароля: входить нечем — и менять нечего."""
        response = client.post("/api/users", json={"name": "безпароля2"})
        try:
            body = response.json()
            assert body["must_change_password"] is False
            assert body["has_password"] is False
            assert "password" not in body
        finally:
            with session_scope() as session:
                row = session.get(AppUser, response.json()["id"])
                if row is not None:
                    session.delete(row)

    def test_the_issued_password_is_returned_once_and_works(self, created) -> None:
        """Показанный пароль обязан подходить — иначе он бесполезен."""
        user_id, issued = created

        assert verify_user(NAME, issued) == user_id

    def test_the_issued_password_matches_the_required_shape(self, created) -> None:
        """15 символов, 3–5 спецсимволов, 2–4 цифры, оба регистра."""
        _, issued = created

        assert len(issued) == TEMP_PASSWORD_LENGTH
        assert 3 <= sum(c in "!@#$%^&*?-_=+" for c in issued) <= 5
        assert 2 <= sum(c.isdigit() for c in issued) <= 4
        assert any(c.islower() for c in issued)
        assert any(c.isupper() for c in issued)


class TestTheFlagActuallyBlocks:
    """Ради этого всё и затевалось: экран можно закрыть, отказ сервера — нет."""

    def test_a_guarded_route_refuses(self, client: TestClient, created) -> None:
        user_id, _ = created
        _acting_as(user_id)
        try:
            response = client.get("/api/downloads")
        finally:
            _stop_acting()

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "password_change_required"

    def test_reading_the_catalog_refuses_too(self, client: TestClient, created) -> None:
        """Даже неявное право не спасает: работать нельзя вообще.

        Каталог доступен каждому вошедшему, поэтому именно он показывает,
        что запрет не сводится к «не хватает права».
        """
        user_id, _ = created
        _acting_as(user_id)
        try:
            response = client.get("/api/catalog/ids")
        finally:
            _stop_acting()

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "password_change_required"

    def test_me_still_answers(self, client: TestClient, created) -> None:
        """Иначе человеку нечем узнать, чего от него хотят."""
        user_id, _ = created
        _acting_as(user_id)
        try:
            response = client.get("/api/users/me")
        finally:
            _stop_acting()

        assert response.status_code == 200
        assert response.json()["must_change_password"] is True


class TestChangingItLiftsTheBlock:
    def test_setting_own_password_clears_the_flag_and_restores_access(
        self, client: TestClient, created
    ) -> None:
        user_id, password = created
        _acting_as(user_id)
        try:
            changed = client.post(
                "/api/users/me/password",
                json={"current": password, "new": "мой-собственный-пароль"},
            )
            assert changed.status_code == 200, changed.text
            assert _row().must_change_password is False

            # Тот же запрос, который только что отвечал отказом. Каталог, а
            # не очередь: прав на очередь у такой записи и не было, и отказ
            # по правам не отличить от отказа по временному паролю.
            assert client.get("/api/catalog/ids").status_code == 200
        finally:
            _stop_acting()

    def test_the_new_password_is_the_one_that_works(self, client: TestClient, created) -> None:
        user_id, password = created
        _acting_as(user_id)
        try:
            client.post(
                "/api/users/me/password",
                json={"current": password, "new": "мой-собственный-пароль"},
            )
        finally:
            _stop_acting()

        assert verify_user(NAME, "мой-собственный-пароль") == user_id
        assert verify_user(NAME, password) is None

    def test_a_short_password_does_not_lift_the_block(
        self, client: TestClient, created
    ) -> None:
        """Отказ на длине не должен случайно снимать флаг."""
        user_id, password = created
        _acting_as(user_id)
        try:
            response = client.post(
                "/api/users/me/password", json={"current": password, "new": "коротк"}
            )
        finally:
            _stop_acting()

        assert response.status_code == 400
        assert _row().must_change_password is True


class TestAdminReset:
    def test_reset_returns_a_usable_password_once(self, client: TestClient, created) -> None:
        user_id, old = created

        response = client.post(f"/api/users/{user_id}/password/reset")

        assert response.status_code == 200, response.text
        issued = response.json()["password"]
        assert len(issued) >= MIN_PASSWORD_LENGTH
        # Показанный пароль действительно подходит — иначе он бесполезен.
        assert verify_user(NAME, issued) == user_id
        assert verify_user(NAME, old) is None

    def test_reset_marks_the_password_temporary_again(self, client: TestClient, created) -> None:
        """Сброшенный пароль знает администратор — значит он тоже временный."""
        user_id, password = created
        _acting_as(user_id)
        try:
            client.post(
                "/api/users/me/password",
                json={"current": password, "new": "мой-собственный-пароль"},
            )
        finally:
            _stop_acting()
        assert _row().must_change_password is False

        client.post(f"/api/users/{user_id}/password/reset")

        assert _row().must_change_password is True

    def test_reset_is_never_the_same_password_twice(self, client: TestClient, created) -> None:
        user_id, _ = created

        first = client.post(f"/api/users/{user_id}/password/reset").json()["password"]
        second = client.post(f"/api/users/{user_id}/password/reset").json()["password"]

        assert first != second

    def test_reset_of_a_missing_user_is_a_404(self, client: TestClient) -> None:
        assert client.post("/api/users/99999/password/reset").status_code == 404
