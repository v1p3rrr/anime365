"""Голоса идентификаторов от Jellyfin — подсказка, а не решение.

Решение пользователя: разметку не переголосовывать, а показывать значок с
подсказкой при наведении. Здесь сторожим две вещи: что расхождение вообще
замечается и что подсказка не горит на ровном месте.

Данные ниже — с живого сервера (200 элементов, `fields=ProviderIds`).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.library import LibraryFolder, LibraryRoot, TitleMapping

SERIES_ID = 995001
ROOT = "/test/jellyfin-verdict"
FOLDER = "Oshi no Ko"

# Настоящий набор от Jellyfin: AniList и Shikimori нумеруют по-разному.
REAL_PROVIDERS = {
    "anilist": "150672",
    "imdb": "tt21030032",
    "kitsu": "46170",
    "shikimori": "39972",
    "tmdb": "203737",
    "tvdb": "421069",
}


def _seed(anilist_id: int | None, mal_id: int | None) -> None:
    with session_scope() as session:
        series = session.get(Series, SERIES_ID)
        if series is None:
            series = Series(id=SERIES_ID, title="Oshi no Ko", type="tv")
            session.add(series)
        series.anilist_id = anilist_id
        series.mal_id = mal_id

        root = session.execute(
            select(LibraryRoot).where(LibraryRoot.path == ROOT)
        ).scalar_one_or_none()
        if root is None:
            root = LibraryRoot(path=ROOT, kind="series")
            session.add(root)
            session.flush()
        session.execute(delete(LibraryFolder).where(LibraryFolder.root_id == root.id))
        session.flush()
        folder = LibraryFolder(root_id=root.id, rel_path=FOLDER, name=FOLDER, video_count=11)
        session.add(folder)
        session.flush()
        session.execute(delete(TitleMapping).where(TitleMapping.series_id == SERIES_ID))
        session.add(
            TitleMapping(
                series_id=SERIES_ID, folder_id=folder.id, source="anilist_id", confidence=1.0
            )
        )


@pytest.fixture(autouse=True)
def _no_leftovers():
    """Убирает за собой корень библиотеки, который заводит `_seed`.

    Корень тут выдуманный — `/test/jellyfin-verdict`, — и лежит он в **общей**
    тестовой базе, одной на весь прогон. Оставшись после этого файла, он
    доставался следующим тестам как обычный доступный корень на запись: строкой
    ниже по алфавиту `test_season_subfolders` просил папку под новую серию и
    получал попытку создать `/test/jellyfin-verdict/Grand Blue`.

    На Windows это «работало»: путь ложился в корень диска C:, папка спокойно
    создавалась в корне диска, тест зеленел. На Linux без прав root — отказ,
    `ensure_target_folder` возвращал None, и падало в двух местах сразу. Нашёл
    это первый прогон CI; до него мусор годами копился на диске разработчика.
    """
    yield
    with session_scope() as session:
        root = session.execute(
            select(LibraryRoot).where(LibraryRoot.path == ROOT)
        ).scalar_one_or_none()
        if root is not None:
            session.execute(delete(LibraryFolder).where(LibraryFolder.root_id == root.id))
            session.delete(root)
        session.execute(delete(TitleMapping).where(TitleMapping.series_id == SERIES_ID))


@pytest.fixture
def jellyfin_says(monkeypatch):
    """Подменяет ответ Jellyfin: настоящий сервер в тестах недоступен."""

    def _install(providers: dict[str, str] | None) -> None:
        from app.services import jellyfin as module

        async def fake_find_item(_self, _rel_paths: list[str]) -> dict[str, Any] | None:
            if providers is None:
                return None
            return {
                # Значение выдуманное: настоящий идентификатор с сервера
                # пользователя в репозитории не нужен, тесту он безразличен.
                "id": "0123456789abcdef0123456789abcdef",
                "type": "Series",
                "path": f"/media/anime/{FOLDER}",
                "providers": providers,
            }

        monkeypatch.setattr(module.JellyfinClient, "find_item", fake_find_item, raising=True)
        monkeypatch.setattr(type(module.jellyfin), "turned_on", property(lambda _s: True))
        monkeypatch.setattr(type(module.jellyfin), "configured", property(lambda _s: True))

    return _install


def _verdict(client) -> dict[str, Any]:
    response = client.get(f"/api/library/jellyfin/verdict/{SERIES_ID}")
    assert response.status_code == 200, response.text
    return response.json()


class TestVerdict:
    def test_anilist_mismatch_is_a_disagreement(self, client, jellyfin_says) -> None:
        _seed(anilist_id=999999, mal_id=None)
        jellyfin_says(REAL_PROVIDERS)

        data = _verdict(client)

        assert data["disagrees"] is True
        anilist = next(row for row in data["ids"] if row["provider"] == "anilist")
        assert anilist["theirs"] == "150672"
        assert anilist["ours"] == 999999

    def test_matching_anilist_is_quiet(self, client, jellyfin_says) -> None:
        _seed(anilist_id=150672, mal_id=None)
        jellyfin_says(REAL_PROVIDERS)

        assert _verdict(client)["disagrees"] is False

    def test_shikimori_difference_alone_does_not_warn(self, client, jellyfin_says) -> None:
        """Иначе значок горел бы почти везде и не значил бы ничего.

        Нумерация Shikimori совпадает с MyAnimeList не всегда: у этого тайтла
        39972 против нашего mal_id, а сам тег вдобавок бывает просто неверным.
        """
        _seed(anilist_id=150672, mal_id=46709)
        jellyfin_says(REAL_PROVIDERS)

        data = _verdict(client)

        assert data["disagrees"] is False
        shikimori = next(row for row in data["ids"] if row["provider"] == "shikimori")
        assert shikimori["differs"] is False
        assert shikimori["theirs"] == "39972", "показать всё равно обязаны"

    def test_foreign_providers_are_shown_but_not_compared(self, client, jellyfin_says) -> None:
        """TMDB, TVDB, Kitsu — по ним человек и опознаёт, что имел в виду Jellyfin."""
        _seed(anilist_id=150672, mal_id=None)
        jellyfin_says(REAL_PROVIDERS)

        rows = {row["provider"]: row for row in _verdict(client)["ids"]}

        assert rows["tmdb"]["theirs"] == "203737"
        assert rows["tmdb"]["ours"] is None
        assert rows["tmdb"]["differs"] is False

    def test_item_not_found_is_not_an_error(self, client, jellyfin_says) -> None:
        _seed(anilist_id=150672, mal_id=None)
        jellyfin_says(None)

        data = _verdict(client)

        assert data == {"available": False, "reason": "not_found"}

    def test_unmapped_title_says_so(self, client, jellyfin_says) -> None:
        _seed(anilist_id=150672, mal_id=None)
        jellyfin_says(REAL_PROVIDERS)
        with session_scope() as session:
            session.execute(delete(TitleMapping).where(TitleMapping.series_id == SERIES_ID))

        assert _verdict(client)["reason"] == "not_mapped"
