"""Заглушка SPA не должна прикрывать несуществующие эндпоинты API.

Приложение — одностраничное, маршрутизация клиентская, поэтому на любой
незнакомый путь сервер отдаёт `index.html`: иначе перезагрузка страницы на
`/library/123` дала бы 404. Правило удобное и ровно одно место, где оно вредит.

Незнакомый путь под `/api/` получал ту же страницу и **код 200**. То есть
опечатка в адресе эндпоинта выглядела как успешный ответ — просто с HTML вместо
JSON. Это не теория: тест входа годами дёргал `/api/catalog`, которого нет
(есть `/api/catalog/ids` и `/api/catalog/cards`), получал 200 от заглушки и
считал, что проверил доступ к каталогу.

Почему не заметили. Заглушка регистрируется, только если каталог со статикой
существует. На машине разработчика он есть — там когда-то собирали фронтенд, и
`backend/app/static/` лежит с тех пор. В CI на чистой машине его нет: фронтенд
собирается отдельной работой и в другом каталоге. Поэтому там тот же запрос
сразу дал честный 404, и обман вскрылся с первого прогона.

Тесты ниже держат обе стороны правила: под `/api/` — только 404, всё остальное
по-прежнему получает приложение.
"""

from __future__ import annotations

from app.main import STATIC_DIR


class TestApiPathsAreNeverCoveredUp:
    def test_unknown_api_path_is_404(self, client) -> None:
        response = client.get("/api/no-such-endpoint")
        assert response.status_code == 404

    def test_the_exact_path_that_lied(self, client) -> None:
        """`/api/catalog` — это префикс, а не маршрут. Ровно на нём и обожглись."""
        assert client.get("/api/catalog?limit=1").status_code == 404

    def test_the_real_endpoint_answers(self, client) -> None:
        """А тот, что существует, отвечает — иначе проверка выше ничего не стоит."""
        assert client.get("/api/catalog/ids?limit=1").status_code == 200

    def test_the_answer_is_not_a_web_page(self, client) -> None:
        """404 обязан быть ответом API, а не страницей приложения."""
        response = client.get("/api/no-such-endpoint")
        assert "text/html" not in response.headers.get("content-type", "")


class TestClientRoutingStillWorks:
    def test_unknown_page_gets_the_app(self, client) -> None:
        """Перезагрузка на клиентском маршруте обязана открывать приложение."""
        if not (STATIC_DIR / "index.html").is_file():
            return  # статика не собрана — заглушки нет вовсе, проверять нечего

        response = client.get("/library/123")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
