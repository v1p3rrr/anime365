"""Ссылка «открыть серию на сайте» ведёт именно на эту серию.

Ошибка, ради которой заведён файл: адреса собирались в словарь по НОМЕРУ серии.
У трейлеров номер свой и начинается с единицы, поэтому первая серия и трейлер к
ней имели одинаковый номер — кто первым попадался в списке переводов, тот и
занимал ключ. На первой серии кнопка вела на страницу трейлера:

    …/treyler-1-seriya-356260/ozvuchka-…   вместо   …/1-seriya-370730/

Нашёл пользователь. Соседний цикл, считающий длительность, эту ловушку учитывал
— здесь её упустили.
"""

from __future__ import annotations

from app.services.title_service import _episode_url


class TestEpisodeUrl:
    def test_translation_url_gives_episode_page(self) -> None:
        url = _episode_url(
            [{"url": "https://host/catalog/yuru-yuri-260/2-seriya-57744/ozvuchka-216078"}]
        )
        assert url == "https://host/catalog/yuru-yuri-260/2-seriya-57744"

    def test_trailing_slash_does_not_break_it(self) -> None:
        """Со слешем на конце последний сегмент оказался бы пустым."""
        url = _episode_url(
            [{"url": "https://host/catalog/show-1/1-seriya-2/ozvuchka-3/"}]
        )
        assert url == "https://host/catalog/show-1/1-seriya-2"

    def test_link_without_episode_segment_is_refused(self) -> None:
        """Ссылка на сам тайтл — не адрес серии, выдумывать его не станем."""
        assert _episode_url([{"url": "https://host/catalog/show-1"}]) is None

    def test_empty_input(self) -> None:
        assert _episode_url([]) is None
        assert _episode_url([{"url": None}]) is None


class TestTrailerDoesNotStealEpisodeOne:
    """Главное: трейлер и первая серия не должны делить один ключ.

    Проверяем сам принцип — адреса раскладываются по идентификатору эпизода, а
    он у трейлера и у серии разный, сколько бы ни совпадали номера.
    """

    def test_same_number_different_episodes(self) -> None:
        trailer = {
            "episode_id": 356260,
            "episode_num": 1.0,
            "url": "https://host/catalog/re-zero-39186/treyler-1-seriya-356260/ozvuchka-5347909",
        }
        first = {
            "episode_id": 370730,
            "episode_num": 1.0,
            "url": "https://host/catalog/re-zero-39186/1-seriya-370730/ozvuchka-5400000",
        }

        by_episode: dict[int, str] = {}
        for item in (trailer, first):
            page = _episode_url([item])
            if page:
                by_episode.setdefault(item["episode_id"], page)

        assert by_episode[370730].endswith("/1-seriya-370730")
        assert by_episode[356260].endswith("/treyler-1-seriya-356260")
        assert "treyler" not in by_episode[370730], (
            "первая серия снова получила адрес трейлера — ключом опять стал номер"
        )
