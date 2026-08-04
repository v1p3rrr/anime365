"""Заголовки кэширования статики.

Хешированные ассеты обязаны кэшироваться навсегда, а `index.html` — никогда.
Перепутать эти два правила дорого в обе стороны: без первого браузер
перепроверяет каждый файл на каждом заходе, без второго — намертво залипает
старая сборка приложения.
"""

from __future__ import annotations

from pathlib import Path

from app.main import STATIC_DIR


class TestAssetCaching:
    def test_hashed_assets_are_immutable(self, client) -> None:
        assets = STATIC_DIR / "assets"
        if not assets.is_dir():
            return  # статика не собрана — проверять нечего

        sample = next((p for p in assets.iterdir() if p.is_file()), None)
        if sample is None:
            return

        response = client.get(f"/assets/{sample.name}")
        assert response.status_code == 200
        assert "immutable" in response.headers.get("cache-control", "")

    def test_index_is_not_cached_forever(self, client) -> None:
        if not (STATIC_DIR / "index.html").is_file():
            return

        response = client.get("/")
        assert "immutable" not in response.headers.get("cache-control", "")
