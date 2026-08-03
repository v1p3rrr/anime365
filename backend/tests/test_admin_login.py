"""Вход по логину и паролю главного администратора.

До этого вход был по одному общему паролю без имени. Пользователь попросил
задавать главного администратора — логин и пароль — через окружение, чтобы
сервер поднимался сразу с рабочей учётной записью.

Совместимость обязательна: вход без логина, по общему паролю, продолжает
работать. Иначе обновление выкинуло бы человека из уже открытых вкладок.
"""

from __future__ import annotations

import pytest

from app.core.bootstrap import ensure_admin_user
from app.core.config import settings
from app.core.db import session_scope
from app.core.security import hash_password, sessions, verify_user
from app.models import DEFAULT_USER_ID, AppUser
from sqlalchemy import select


def _restore_users(*names: str) -> None:
    """Убирает следы теста, не трогая пользователя по умолчанию.

    `ensure_admin_user` намеренно ПЕРЕИМЕНОВЫВАЕТ пользователя по умолчанию, а
    не заводит второго рядом: к нему привязаны задачи и права. Возвращать имя
    обратно не пытаемся — оно могло быть занято, и переименование падало бы на
    уникальности. Достаточно снять пароль и убрать записи, которых до теста не
    было.
    """
    with session_scope() as session:
        for name in names:
            user = session.execute(
                select(AppUser).where(AppUser.name == name)
            ).scalar_one_or_none()
            if user is None:
                continue
            user.password_hash = None
            if user.id != DEFAULT_USER_ID:
                session.delete(user)


@pytest.fixture
def admin():
    """Администратор с известным паролем, заведённый штатным путём."""
    settings.set_db_override("admin_login", "бочка")
    settings.set_db_override("ui_password", "секрет-администратора")
    ensure_admin_user()
    yield "бочка", "секрет-администратора"
    settings.set_db_override("admin_login", None)
    settings.set_db_override("ui_password", None)
    sessions.drop_all()
    _restore_users("бочка")


class TestEnsureAdminUser:
    def test_creates_user_with_password(self, admin) -> None:
        login, password = admin
        with session_scope() as session:
            user = session.execute(select(AppUser).where(AppUser.name == login)).scalar_one()
            assert user.is_admin
            # Именно хеш: по нему нельзя войти, даже получив копию базы.
            assert user.password_hash and user.password_hash.startswith("$argon2")
            assert password not in user.password_hash

    def test_password_already_hashed_is_not_hashed_twice(self) -> None:
        """Пароль в настройке может быть уже хешем — его туда кладёт UI.

        Второй проход хеширования сделал бы вход невозможным.
        """
        settings.set_db_override("admin_login", "двойной")
        settings.set_db_override("ui_password", hash_password("исходный"))
        try:
            ensure_admin_user()
            assert verify_user("двойной", "исходный") is not None
        finally:
            settings.set_db_override("admin_login", None)
            settings.set_db_override("ui_password", None)
            _restore_users("двойной")

    def test_admin_flag_is_restored(self, admin) -> None:
        """Снятая галочка администратора не должна запирать снаружи."""
        login, _ = admin
        with session_scope() as session:
            session.execute(select(AppUser).where(AppUser.name == login)).scalar_one().is_admin = (
                False
            )

        ensure_admin_user()

        with session_scope() as session:
            assert session.execute(select(AppUser).where(AppUser.name == login)).scalar_one().is_admin


class TestVerifyUser:
    def test_correct_pair(self, admin) -> None:
        login, password = admin
        assert verify_user(login, password) is not None

    def test_wrong_password(self, admin) -> None:
        login, _ = admin
        assert verify_user(login, "не тот") is None

    def test_unknown_login(self, admin) -> None:
        assert verify_user("нет-такого", "любой") is None

    def test_user_without_password_cannot_log_in(self) -> None:
        """Пользователь без пароля — владелец задач и прав, но не учётка."""
        # Убираем и ДО теста: файл тестовой БД переживает прогоны, и остаток от
        # прошлого падения ронял бы этот тест на уникальности имени.
        _restore_users("безпароля")
        with session_scope() as session:
            session.add(AppUser(name="безпароля", is_admin=False, permissions=[]))
        try:
            assert verify_user("безпароля", "") is None
            assert verify_user("безпароля", "что угодно") is None
        finally:
            _restore_users("безпароля")


class TestLoginEndpoint:
    def test_login_with_credentials(self, client, admin) -> None:
        login, password = admin
        response = client.post("/api/auth/login", json={"login": login, "password": password})
        assert response.status_code == 200
        assert client.get("/api/settings").status_code == 200

    def test_wrong_credentials_are_refused(self, client, admin) -> None:
        login, _ = admin
        response = client.post("/api/auth/login", json={"login": login, "password": "мимо"})
        assert response.status_code == 401
        # Сообщение одно на оба случая: иначе видно, какие имена существуют.
        assert "логин или пароль" in response.json()["detail"]

    def test_shared_password_still_works(self, client, admin) -> None:
        """Совместимость: вход без логина никуда не делся."""
        _, password = admin
        assert client.post("/api/auth/login", json={"password": password}).status_code == 200
        assert client.get("/api/settings").status_code == 200
