"""Жизненный цикл пароля: смена, отзыв сессий, минимальные требования.

Файл заведён после ревизии авторизации, и первый тест здесь — регрессионный на
настоящую ошибку: **смена пароля из интерфейса не меняла пароль**. Настройка
`ui_password` обновлялась, а хеш у самой учётной записи — нет, потому что
переносил его туда только `ensure_admin_user()` при старте. То есть до
перезапуска контейнера продолжал работать старый пароль, а новый не работал
вовсе — ровно наоборот тому, чего ждёт человек, меняющий пароль после утечки.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.bootstrap import ensure_admin_user
from app.core.config import settings
from app.core.db import session_scope
from app.core.security import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    password_problem,
    sessions,
    verify_user,
)
from app.models import DEFAULT_USER_ID, AppUser

ADMIN = "корень"
ADMIN_PASSWORD = "первый-пароль"


def _cleanup(*names: str) -> None:
    """Убирает следы теста, не трогая пользователя по умолчанию."""
    with session_scope() as session:
        for name in names:
            row = session.execute(
                select(AppUser).where(AppUser.name == name)
            ).scalar_one_or_none()
            if row is None:
                continue
            row.password_hash = None
            if row.id != DEFAULT_USER_ID:
                session.delete(row)


@pytest.fixture
def admin():
    """Администратор, заведённый штатным путём.

    ⚠️ Настройки кладём **строками в таблицу**, а не через `set_db_override`.
    Эндпоинт настроек после сохранения зовёт `load_settings_from_db()`, а тот
    заменяет слой БД целиком — оверрайд, выставленный в обход таблицы, при этом
    пропадает, и `ensure_admin_user()` начинает работать с логином по умолчанию.
    В бою этого не бывает: там имя приходит из окружения и переживает
    перезагрузку слоя.
    """
    from app.core.bootstrap import load_settings_from_db
    from app.models.settings import AppSetting

    with session_scope() as session:
        session.merge(AppSetting(key="admin_login", value=ADMIN))
        session.merge(AppSetting(key="ui_password", value=ADMIN_PASSWORD))
    load_settings_from_db()
    ensure_admin_user()

    yield ADMIN, ADMIN_PASSWORD

    with session_scope() as session:
        for key in ("admin_login", "ui_password"):
            row = session.get(AppSetting, key)
            if row is not None:
                session.delete(row)
    load_settings_from_db()
    sessions.drop_all()
    _cleanup(ADMIN)


class TestPasswordChangeActuallyChangesPassword:
    def test_setting_the_password_updates_the_account(self, client: TestClient, admin) -> None:
        """Главный регрессионный тест: новый пароль работает, старый — нет."""
        login, old = admin
        assert client.post(
            "/api/auth/login", json={"login": login, "password": old}
        ).status_code == 200

        response = client.put(
            "/api/settings",
            json={"updates": [{"key": "ui_password", "value": "новый-длинный-пароль"}]},
        )
        assert response.status_code == 200, response.text

        assert verify_user(login, "новый-длинный-пароль") is not None
        assert verify_user(login, old) is None

    def test_reset_drops_the_password_set_from_the_ui(self, client: TestClient, admin) -> None:
        """Сброшенный пароль перестаёт открывать вход.

        Сброс убирает переопределение, и действующим становится значение из
        окружения. В тестах его нет, то есть пароль исчезает совсем — и вместе с
        ним обязан исчезнуть хеш у учётной записи. Иначе выходит нелепое:
        настройка пуста, а войти по сброшенному паролю по-прежнему можно.
        """
        login, password = admin
        client.post("/api/auth/login", json={"login": login, "password": password})
        client.put(
            "/api/settings",
            json={"updates": [{"key": "ui_password", "value": "временный-пароль"}]},
        )
        assert verify_user(login, "временный-пароль") is not None

        assert client.post("/api/settings/reset/ui_password").status_code == 200
        assert verify_user(login, "временный-пароль") is None


class TestSessionAfterPasswordChange:
    def test_new_session_belongs_to_the_same_user(self, client: TestClient, admin) -> None:
        """Сессия после смены пароля остаётся СВОЕЙ.

        Раньше здесь подставлялся пользователь по умолчанию, и администратор,
        не являющийся корневым, продолжал работу под чужой учётной записью.
        """
        login, password = admin
        client.post("/api/auth/login", json={"login": login, "password": password})
        me_before = client.get("/api/users/me").json()

        client.put(
            "/api/settings",
            json={"updates": [{"key": "ui_password", "value": "ещё-один-пароль"}]},
        )

        me_after = client.get("/api/users/me").json()
        assert me_after["id"] == me_before["id"]
        assert me_after["name"] == me_before["name"]


class TestOwnPasswordChange:
    """Свой пароль меняет любой вошедший — иначе он навсегда чужой."""

    def test_requires_the_current_password(self, client: TestClient, admin) -> None:
        login, password = admin
        client.post("/api/auth/login", json={"login": login, "password": password})

        response = client.post(
            "/api/users/me/password",
            json={"current": "не тот", "new": "достаточно-длинный"},
        )
        assert response.status_code == 403
        # Пароль остался прежним — отказ не должен ничего менять.
        assert verify_user(login, password) is not None

    def test_changes_and_keeps_the_caller_signed_in(self, client: TestClient, admin) -> None:
        login, password = admin
        client.post("/api/auth/login", json={"login": login, "password": password})

        response = client.post(
            "/api/users/me/password",
            json={"current": password, "new": "мой-собственный-пароль"},
        )
        assert response.status_code == 200, response.text
        assert verify_user(login, "мой-собственный-пароль") is not None
        # Сменивший пароль не должен немедленно получить 401 — иначе это
        # выглядит как поломка, а не как успешная смена.
        assert client.get("/api/users/me").status_code == 200

    def test_short_password_is_refused(self, client: TestClient, admin) -> None:
        login, password = admin
        client.post("/api/auth/login", json={"login": login, "password": password})

        response = client.post(
            "/api/users/me/password", json={"current": password, "new": "1234"}
        )
        assert response.status_code == 400
        assert verify_user(login, password) is not None


class TestPasswordRequirements:
    def test_too_short(self) -> None:
        assert password_problem("a" * (MIN_PASSWORD_LENGTH - 1)) is not None

    def test_long_enough(self) -> None:
        assert password_problem("a" * MIN_PASSWORD_LENGTH) is None

    def test_surrounding_space_is_refused(self) -> None:
        """Пробел по краям почти всегда случаен, а войти потом не выходит."""
        assert password_problem(" пароль-с-пробелом ") is not None

    def test_created_user_password_is_checked(self, client: TestClient, admin) -> None:
        login, password = admin
        client.post("/api/auth/login", json={"login": login, "password": password})
        try:
            response = client.post(
                "/api/users",
                json={"name": "короткий", "is_admin": False, "password": "123"},
            )
            assert response.status_code == 400
        finally:
            _cleanup("короткий")


class TestSessionsAreRevoked:
    def test_deleting_a_user_drops_their_sessions(self, client: TestClient, admin) -> None:
        login, password = admin
        client.post("/api/auth/login", json={"login": login, "password": password})

        with session_scope() as session:
            victim = AppUser(
                name="жертва",
                is_admin=False,
                permissions=[],
                password_hash=hash_password("пароль-жертвы"),
            )
            session.add(victim)
            session.flush()
            victim_id = victim.id

        token = sessions.create(victim_id)
        assert sessions.get(token) is not None

        try:
            assert client.delete(f"/api/users/{victim_id}").status_code == 200
            assert sessions.get(token) is None
        finally:
            _cleanup("жертва")

    def test_changing_someones_password_drops_their_sessions(
        self, client: TestClient, admin
    ) -> None:
        login, password = admin
        client.post("/api/auth/login", json={"login": login, "password": password})

        with session_scope() as session:
            other = AppUser(
                name="другой",
                is_admin=False,
                permissions=[],
                password_hash=hash_password("старый-пароль-другого"),
            )
            session.add(other)
            session.flush()
            other_id = other.id

        token = sessions.create(other_id)
        try:
            assert client.put(
                f"/api/users/{other_id}", json={"password": "новый-пароль-другого"}
            ).status_code == 200
            # Смысл смены пароля — выкинуть того, кто вошёл со старым.
            assert sessions.get(token) is None
        finally:
            _cleanup("другой")
