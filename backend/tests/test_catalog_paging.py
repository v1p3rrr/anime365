"""Постраничная выдача идентификаторов каталога.

Раньше `/catalog/ids` отдавал весь список — 29 тысяч чисел на каждую смену
фильтра. Тесты следят за двумя вещами, которые легко разъезжаются: окно должно
резать выдачу, а `total` обязан оставаться полным — по нему рисуются и полоса
прокрутки, и номера страниц.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestWindow:
    def test_default_window_is_capped(self, client: TestClient):
        body = client.get("/api/catalog/ids").json()
        assert len(body["ids"]) <= body["limit"]
        assert body["limit"] == 200

    def test_total_counts_the_whole_selection(self, client: TestClient):
        body = client.get("/api/catalog/ids?limit=5").json()
        assert len(body["ids"]) <= 5
        # Полный размер выборки, а не длина окна: иначе нумерация страниц
        # показывала бы одну страницу при любом каталоге.
        assert body["total"] >= len(body["ids"])

    def test_offset_moves_the_window(self, client: TestClient):
        first = client.get("/api/catalog/ids?limit=5").json()
        second = client.get("/api/catalog/ids?limit=5&offset=5").json()
        if first["total"] > 5:
            assert first["ids"] != second["ids"]
            # Окна не должны пересекаться — иначе при подгрузке пойдут дубли.
            assert not set(first["ids"]) & set(second["ids"])

    def test_total_is_stable_across_windows(self, client: TestClient):
        first = client.get("/api/catalog/ids?limit=5").json()
        second = client.get("/api/catalog/ids?limit=5&offset=5").json()
        assert first["total"] == second["total"]

    def test_offset_past_the_end_returns_empty(self, client: TestClient):
        total = client.get("/api/catalog/ids?limit=1").json()["total"]
        body = client.get(f"/api/catalog/ids?limit=5&offset={total + 100}").json()
        assert body["ids"] == []
        assert body["total"] == total


class TestLimits:
    def test_limit_above_maximum_is_refused(self, client: TestClient):
        # Запрет, а не молчаливое усечение: иначе клиент считал бы, что получил
        # больше, чем на самом деле, и пропускал бы часть каталога.
        assert client.get("/api/catalog/ids?limit=100000").status_code == 422

    def test_negative_offset_is_refused(self, client: TestClient):
        assert client.get("/api/catalog/ids?offset=-1").status_code == 422


class TestFiltersStillApply:
    def test_window_respects_the_filter(self, client: TestClient):
        everything = client.get("/api/catalog/ids?limit=1").json()
        filtered = client.get("/api/catalog/ids?limit=1&type=movie").json()
        # Фильтр обязан менять именно total, а не только содержимое окна.
        assert filtered["total"] <= everything["total"]
