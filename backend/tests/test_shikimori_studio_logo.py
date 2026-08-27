"""Ссылка на логотип студии отдаётся только тогда, когда её можно обслужить.

⚠️ Найдено ревью на живых данных. Путь логотипа приходит от Шикимори, и у части
студий он **без расширения**:

    Bones (4)            /system/studios/original/4.png?1311292711   -> png
    Toei Animation (18)  /system/studios/original/18.?1446981470     -> ''

Из второго получался адрес `/api/shikimori/studio/18.`, который `studio_logo()`
всё равно отвергает по белому списку расширений. То есть браузеру отдавалась
заведомо мёртвая ссылка — ровно то, чего отбор и должен избегать: комментарий
рядом обещал «если картинки нет, ссылку не даём вовсе».

Замерено на 12 студиях из двадцати тайтлов: таких две, Toei Animation и
Kinema Citrus. Toei встречается часто.
"""

from __future__ import annotations

import pytest

from app.services.shikimori import (
    STUDIO_LOGO_EXTENSIONS,
    _logo_ext,
    _studio_logo_url,
)


class TestStudioLogoUrl:
    def test_a_normal_path_gives_our_address(self) -> None:
        item = {"id": 4, "name": "Bones", "image": "/system/studios/original/4.png?1311292711"}
        assert _studio_logo_url(item) == "/api/shikimori/studio/4.png"

    def test_a_path_without_an_extension_gives_nothing(self) -> None:
        # Настоящий путь Toei Animation: точка есть, расширения за ней нет.
        item = {"id": 18, "name": "Toei", "image": "/system/studios/original/18.?1446981470"}
        assert _studio_logo_url(item) is None

    def test_a_path_without_any_dot_gives_nothing(self) -> None:
        item = {"id": 7, "name": "X", "image": "/system/studios/original/7?123"}
        assert _studio_logo_url(item) is None

    def test_the_placeholder_gives_nothing(self) -> None:
        # У Шикимори «картинки нет» — это ссылка на заглушку, а не пустое поле.
        item = {"id": 9, "name": "Y", "image": "/assets/globals/missing_original.jpg"}
        assert _studio_logo_url(item) is None

    def test_an_empty_field_gives_nothing(self) -> None:
        assert _studio_logo_url({"id": 1, "name": "Z", "image": ""}) is None
        assert _studio_logo_url({"id": 1, "name": "Z"}) is None

    @pytest.mark.parametrize("ext", sorted(STUDIO_LOGO_EXTENSIONS))
    def test_every_allowed_extension_survives(self, ext: str) -> None:
        # Обратная сторона: отбор не должен резать то, что обслуживается.
        item = {"id": 42, "name": "S", "image": f"/system/studios/original/42.{ext}?1"}
        assert _studio_logo_url(item) == f"/api/shikimori/studio/42.{ext}"

    def test_an_unknown_extension_gives_nothing(self) -> None:
        item = {"id": 42, "name": "S", "image": "/system/studios/original/42.svg?1"}
        assert _logo_ext(item["image"]) == "svg"
        assert _studio_logo_url(item) is None, "svg не обслуживается studio_logo()"


class TestLogoExt:
    @pytest.mark.parametrize(
        "path, expected",
        [
            ("/system/studios/original/314.jpg?123", "jpg"),
            ("/system/studios/original/314.PNG", "png"),
            ("/system/studios/original/18.?1446981470", ""),
        ],
    )
    def test_extension_is_taken_before_the_query(self, path: str, expected: str) -> None:
        assert _logo_ext(path) == expected
