"""Точные ступени качества читаются по тем же сериям, которые уточняются.

⚠️ Заведено по расхождению двух функций, обязанных смотреть на одно и то же.
`QualityRefiner._pick_representatives` несодержательные эпизоды пропускает —
трейлеры уточнять незачем. А `exact_heights_for_series` брала **все** эпизоды
подряд и подбирала кэш трейлера, приписывая его настоящей серии: нумерация у
трейлеров своя и начинается с единицы, так что кэш «трейлера №1» ложился на
первую серию.

Пара к `TestQualitySummaryIgnoresNonContent` в `test_coverage.py`: там та же
болезнь на стороне оценки, здесь — на стороне точных данных.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Episode, Series, Translation, TranslationEmbed
from app.services.title_service import exact_heights_for_series, quality_refiner

SERIES = 980_090
TV_EPISODE = 371_900
TRAILER_EPISODE = 371_901
TV_TRANSLATION = 570_900
TRAILER_TRANSLATION = 570_901


@pytest.fixture(autouse=True)
def clean():
    def wipe() -> None:
        with session_scope() as session:
            session.execute(
                delete(TranslationEmbed).where(
                    TranslationEmbed.translation_id.in_([TV_TRANSLATION, TRAILER_TRANSLATION])
                )
            )
            session.execute(delete(Translation).where(Translation.series_id == SERIES))
            session.execute(delete(Episode).where(Episode.series_id == SERIES))
            session.execute(delete(Series).where(Series.id == SERIES))

    wipe()
    yield
    wipe()


def _seed() -> None:
    """Первая серия и трейлер к ней. У обоих `episode_num` равен единице."""
    with session_scope() as session:
        session.merge(
            Series(id=SERIES, title="Trailer Trap", type="tv", number_of_episodes=1,
                   has_details=True)
        )
        session.merge(
            Episode(id=TV_EPISODE, series_id=SERIES, episode_int="1", episode_num=1.0,
                    episode_type="tv", episode_full="1 серия", is_active=True)
        )
        session.merge(
            Episode(id=TRAILER_EPISODE, series_id=SERIES, episode_int="1", episode_num=1.0,
                    episode_type="preview", episode_full="Трейлер 1 серия", is_active=True)
        )
        session.merge(
            Translation(id=TV_TRANSLATION, series_id=SERIES, episode_id=TV_EPISODE,
                        type_lang="ru", type_kind="sub", height=720, is_active=True)
        )
        session.merge(
            Translation(id=TRAILER_TRANSLATION, series_id=SERIES,
                        episode_id=TRAILER_EPISODE, type_lang="ru", type_kind="sub",
                        height=1080, is_active=True)
        )


def _cache(translation_id: int, heights: list[int]) -> None:
    from datetime import datetime

    with session_scope() as session:
        session.merge(
            TranslationEmbed(
                translation_id=translation_id,
                has_subtitles=True,
                download_heights=heights,
                stream_heights=heights,
                download_sizes={},
                fetched_at=datetime.utcnow(),
            )
        )


class TestExactHeightsSkipNonContent:
    def test_trailer_cache_does_not_reach_the_episode(self) -> None:
        _seed()
        _cache(TV_TRANSLATION, [720, 360])
        _cache(TRAILER_TRANSLATION, [1080, 720, 360])

        assert exact_heights_for_series(SERIES) == {1.0: [720, 360]}

    def test_trailer_alone_yields_nothing(self) -> None:
        # У настоящей серии кэша нет — значит и точных данных быть не должно.
        # Раньше её ступени брались у трейлера.
        _seed()
        _cache(TRAILER_TRANSLATION, [1080, 720])

        assert exact_heights_for_series(SERIES) == {}

    def test_real_episode_still_read(self) -> None:
        # Обратная сторона: отсев не имеет права терять содержательное.
        _seed()
        _cache(TV_TRANSLATION, [1080, 720, 360])

        assert exact_heights_for_series(SERIES) == {1.0: [1080, 720, 360]}

    def test_refiner_and_reader_agree_on_scope(self) -> None:
        # Смысл правки: две функции обязаны смотреть на один и тот же набор.
        _seed()
        picked = quality_refiner._pick_representatives(SERIES)
        assert picked == {1.0: TV_TRANSLATION}, "уточняется только настоящая серия"

        _cache(TV_TRANSLATION, [720])
        _cache(TRAILER_TRANSLATION, [1080])
        assert set(exact_heights_for_series(SERIES)) == set(picked)
