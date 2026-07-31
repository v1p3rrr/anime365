"""Разграничение прав.

Заготовка под многопользовательский режим, которого пока нет. Тесты здесь важнее
обычного по двум причинам.

Первая: это граница безопасности. Заявление «в обход интерфейса выполнить нельзя»
должно быть проверено запросом, а не подкреплено тем, что кнопка спрятана.

Вторая: сегодня разграничение ни на что не влияет — пользователь один и он
администратор. Незамеченная поломка проявилась бы только при включении режима,
то есть спустя недели после того, как её внесли.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.db import session_scope
from app.core.permissions import ALL_PERMISSIONS, CurrentUser, Permission
from app.models import DEFAULT_USER_ID, AppUser


class TestCurrentUser:
    def test_admin_can_everything_without_listed_permissions(self):
        # Администратору права не перечисляют: иначе достаточно забыть одну
        # галочку, чтобы он потерял доступ к собственным настройкам.
        admin = CurrentUser(1, "root", is_admin=True, permissions=[])
        assert all(admin.can(p) for p in Permission)

    def test_plain_user_limited_to_granted(self):
        viewer = CurrentUser(2, "гость", is_admin=False, permissions=[Permission.WATCH.value])
        assert viewer.can(Permission.WATCH)
        assert not viewer.can(Permission.DOWNLOADS)
        assert not viewer.can(Permission.SETTINGS)
        assert not viewer.can(Permission.LIBRARY)

    def test_empty_permissions_means_nothing(self):
        nobody = CurrentUser(3, "никто", is_admin=False, permissions=[])
        assert not any(nobody.can(p) for p in Permission)


@pytest.fixture(autouse=True)
def only_default_user():
    """Убирает всех, кроме пользователя по умолчанию.

    База у тестов общая и переживает прогоны, а часть тестов заводит записи.
    Без уборки упавший прогон оставлял бы их за собой, и следующий падал уже по
    другой причине — «пользователь уже есть» вместо проверяемого поведения.
    """

    def purge():
        with session_scope() as session:
            for row in session.query(AppUser).filter(AppUser.id != DEFAULT_USER_ID).all():
                session.delete(row)
            default = session.get(AppUser, DEFAULT_USER_ID)
            if default is not None:
                # Тесты про последнего администратора его понижают — возвращаем.
                default.is_admin = True

    purge()
    yield
    purge()


@pytest.fixture
def viewer_client(client: TestClient):
    """Клиент, работающий от пользователя с одним лишь правом просмотра.

    Подменяем зависимость, а не заводим сессию: вход пока общий по паролю, и
    настоящего многопользовательского логина ещё нет — а проверять поведение
    эндпоинтов надо уже сейчас.
    """
    from app.core.permissions import current_user
    from app.main import app

    viewer = CurrentUser(99, "гость", is_admin=False, permissions=[Permission.WATCH.value])
    app.dependency_overrides[current_user] = lambda: viewer
    yield client
    app.dependency_overrides.pop(current_user, None)


class TestServerSideEnforcement:
    """Отказ должен приходить от сервера, а не от спрятанной кнопки."""

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("put", "/api/settings", {"updates": [{"key": "access_token", "value": "x"}]}),
            ("post", "/api/settings/reset/access_token", None),
            ("post", "/api/downloads", {"translation_id": 1, "kind": "video"}),
            ("post", "/api/downloads/pause-all", None),
            ("post", "/api/library/scan", None),
            ("post", "/api/library/mappings", {"folder_id": 1, "series_id": 1}),
        ],
    )
    def test_viewer_is_refused(self, viewer_client: TestClient, method, path, body):
        response = getattr(viewer_client, method)(path, json=body)
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "permission_denied"

    def test_viewer_keeps_what_was_granted(self, viewer_client: TestClient):
        # Право на просмотр выдано, поэтому отказ должен быть не про права.
        # Что запрос упрётся в отсутствие токена — нормально и ожидаемо.
        response = viewer_client.get("/api/stream/1/video")
        assert response.status_code != 403 or (
            response.json().get("detail", {}).get("code") != "permission_denied"
        )

    def test_reading_stays_open(self, viewer_client: TestClient):
        # Смотреть очередь и каталог можно и без права менять их: иначе роль
        # «только просмотр» означала бы пустой интерфейс.
        assert viewer_client.get("/api/downloads").status_code == 200
        assert viewer_client.get("/api/catalog/genres").status_code == 200


class TestDefaultInstallIsUnrestricted:
    """Пока пользователь один, ничего не должно измениться."""

    def test_default_user_is_admin(self, client: TestClient):
        with session_scope() as session:
            row = session.get(AppUser, DEFAULT_USER_ID)
            assert row is not None
            assert row.is_admin is True

    def test_me_reports_full_rights(self, client: TestClient):
        body = client.get("/api/users/me").json()
        assert body["is_admin"] is True
        assert sorted(body["permissions"]) == sorted(ALL_PERMISSIONS)
        # Флаг отделяет «разграничение не действует» от «прав нет»: интерфейс
        # по нему решает, прятать ли что-то вообще.
        assert body["multiuser_enabled"] is False

    def test_mutations_are_not_blocked(self, client: TestClient):
        # Запрос может упасть по своей причине, но не по правам.
        response = client.post("/api/library/scan")
        if response.status_code == 403:
            assert response.json()["detail"]["code"] != "permission_denied"


class TestLastAdminIsProtected:
    def test_cannot_strip_admin_from_the_only_one(self, client: TestClient):
        response = client.put(f"/api/users/{DEFAULT_USER_ID}", json={"is_admin": False})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "cannot_remove_last_admin"

    def test_cannot_delete_the_only_admin(self, client: TestClient):
        response = client.delete(f"/api/users/{DEFAULT_USER_ID}")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "cannot_remove_last_admin"

    def test_can_demote_once_another_admin_exists(self, client: TestClient):
        created = client.post("/api/users", json={"name": "второй", "is_admin": True})
        assert created.status_code == 200, created.text

        response = client.put(f"/api/users/{DEFAULT_USER_ID}", json={"is_admin": False})
        assert response.status_code == 200
        assert response.json()["is_admin"] is False

        # Возвращаем как было: тесты делят одну базу.
        client.put(f"/api/users/{DEFAULT_USER_ID}", json={"is_admin": True})
        client.delete(f"/api/users/{created.json()['id']}")


class TestUserCreation:
    def test_duplicate_name_is_refused(self, client: TestClient):
        first = client.post("/api/users", json={"name": "Дубль"})
        assert first.status_code == 200
        # Регистр не должен создавать «второго» пользователя с тем же именем.
        second = client.post("/api/users", json={"name": "дубль"})
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "user_exists"
        client.delete(f"/api/users/{first.json()['id']}")

    def test_unknown_permission_is_refused(self, client: TestClient):
        response = client.post(
            "/api/users", json={"name": "кривой", "permissions": ["settings", "выдумка"]}
        )
        assert response.status_code == 400

    def test_permissions_are_stored_in_canonical_order(self, client: TestClient):
        created = client.post(
            "/api/users",
            json={"name": "порядок", "permissions": ["watch", "settings"]},
        )
        assert created.status_code == 200
        # Порядок канонический, а не порядок нажатий — иначе список в интерфейсе
        # перетасовывался бы при каждом сохранении.
        assert created.json()["permissions"] == [
            p for p in ALL_PERMISSIONS if p in ("watch", "settings")
        ]
        client.delete(f"/api/users/{created.json()['id']}")

    def test_admin_gets_full_set_regardless_of_input(self, client: TestClient):
        created = client.post(
            "/api/users", json={"name": "начальник", "is_admin": True, "permissions": []}
        )
        assert created.status_code == 200
        assert created.json()["permissions"] == ALL_PERMISSIONS
        client.delete(f"/api/users/{created.json()['id']}")
