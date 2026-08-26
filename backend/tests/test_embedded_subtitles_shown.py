"""Субтитры внутри видеофайла видны на странице тайтла.

⚠️ Заведено по живому расхождению, а не на всякий случай. У пользователя
страница показывала «Субтитры RU: нет» у первых трёх серий, а автоскачивание
ровно эти серии считало закрытыми и не качало ничего. Оба ответа были
«правильными» по-своему:

* страница считает наличие по **разметке серий**, то есть по соседним файлам,
  и дорожка внутри контейнера туда не попадает никогда;
* автоскачивание дорожки внутри контейнера учитывает — на то и галка
  «Не считать дорожку в файле за наличие субтитров».

Вместе они врали: движок отказывался качать, а страница показывала, что качать
нужно, и зацепиться человеку было не за что. Разбор занял вечер.

Настоящий случай: релиз Erai-raws, у которого в `.nfo` стоит
`subtitle_languages: ["jpn", "rus"]`.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Episode, Series, Translation
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, LibraryRoot
from app.services.title_service import build_title_page

SERIES = 980_070
EPISODE_ID = 371_700
ROOT = "/test/embedded-subs"

#: Ровно то, что Jellyfin записал в `.nfo` у релиза Erai-raws.
ERAI_STREAMS = {"subtitle_languages": ["jpn", "rus"], "audio_languages": ["jpn"]}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(
        "app.services.title_service.schedule_translation_refresh", lambda series_id: False
    )

    def wipe() -> None:
        with session_scope() as session:
            session.execute(delete(EpisodeMapping).where(EpisodeMapping.series_id == SERIES))
            session.execute(delete(Translation).where(Translation.series_id == SERIES))
            session.execute(delete(Episode).where(Episode.series_id == SERIES))
            session.execute(delete(Series).where(Series.id == SERIES))
            root = session.execute(
                select(LibraryRoot).where(LibraryRoot.path == ROOT)
            ).scalar_one_or_none()
            if root is not None:
                folders = list(
                    session.execute(
                        select(LibraryFolder).where(LibraryFolder.root_id == root.id)
                    ).scalars()
                )
                for folder in folders:
                    session.execute(
                        delete(LibraryFile).where(LibraryFile.folder_id == folder.id)
                    )
                session.execute(delete(LibraryFolder).where(LibraryFolder.root_id == root.id))
                session.delete(root)

    wipe()
    yield
    wipe()


def _seed(streams: dict | None, *, sidecar: str | None = None) -> None:
    """Тайтл с одной серией и одним видеофайлом."""
    with session_scope() as session:
        session.merge(
            Series(id=SERIES, title="Erai Release", type="tv", number_of_episodes=1,
                   has_details=True)
        )
        session.merge(
            Episode(
                id=EPISODE_ID,
                series_id=SERIES,
                episode_int="1",
                episode_num=1.0,
                episode_type="tv",
                episode_full="1 серия",
                is_active=True,
            )
        )
        root = LibraryRoot(path=ROOT, kind="series")
        session.add(root)
        session.flush()
        folder = LibraryFolder(root_id=root.id, rel_path="Erai", name="Erai")
        session.add(folder)
        session.flush()

        video = LibraryFile(
            folder_id=folder.id,
            filename="Erai S01E01 [Erai-raws] WEBRip-1080p.mkv",
            kind="video",
            size_bytes=100,
            mtime=1.0,
            episode=1.0,
            streams=streams,
        )
        session.add(video)
        session.flush()
        session.add(
            EpisodeMapping(series_id=SERIES, episode=1.0, file_id=video.id, role="video")
        )

        if sidecar:
            subs = LibraryFile(
                folder_id=folder.id,
                filename=f"Erai S01E01 [Erai-raws] WEBRip-1080p.{sidecar}.ass",
                kind="subtitle",
                lang=sidecar,
                size_bytes=10,
                mtime=1.0,
                episode=1.0,
            )
            session.add(subs)
            session.flush()
            session.add(
                EpisodeMapping(
                    series_id=SERIES, episode=1.0, file_id=subs.id, role=f"sub_{sidecar}"
                )
            )


def _row() -> dict:
    page = asyncio.run(build_title_page(SERIES))
    assert page is not None
    return page["episodes"][0]


class TestEmbeddedTracksReachThePage:
    def test_a_russian_track_inside_the_file_is_reported(self) -> None:
        _seed(ERAI_STREAMS)

        assert _row()["embedded_subs"] == ["ja", "ru"]

    def test_it_is_not_mixed_into_have(self) -> None:
        """Разные состояния: файла рядом нет, а субтитры есть.

        Смешать значило бы сказать «есть локально» про то, чего отдельным
        файлом не существует, — и сломать выбор в панели загрузки.
        """
        _seed(ERAI_STREAMS)
        row = _row()

        assert "sub_ru" not in row["have"]
        assert "video" in row["have"]

    def test_a_sidecar_and_a_track_are_both_visible(self) -> None:
        _seed(ERAI_STREAMS, sidecar="ru")
        row = _row()

        assert "sub_ru" in row["have"], "соседний файл считается как раньше"
        assert "ru" in row["embedded_subs"]

    def test_unknown_stays_an_empty_list(self) -> None:
        """`.nfo` есть не у всех, ffprobe под тумблером — «не проверяли» это не «нет»."""
        _seed(None)

        assert _row()["embedded_subs"] == []

    def test_a_file_without_subtitle_tracks_reports_nothing(self) -> None:
        _seed({"audio_languages": ["jpn"]})

        assert _row()["embedded_subs"] == []


class TestThePageAgreesWithAutodownload:
    """Главное здесь. Две подсистемы обязаны отвечать одно и то же.

    Раньше страница писала «нет», движок отвечал «есть», и разошлись они молча.
    """

    def test_what_the_engine_skips_the_page_explains(self) -> None:
        from app.services.autodownload import missing_episodes
        from app.services.autodownload_rules import Resolved

        _seed(ERAI_STREAMS)
        rule = Resolved(lang="ru", priorities=["crunchyroll"])

        skipped = missing_episodes(SERIES, rule) == []
        explained = "ru" in _row()["embedded_subs"]

        assert skipped, "движок считает серию закрытой из-за дорожки в файле"
        assert explained, "и страница обязана это показать, а не писать «нет»"

    def test_with_the_toggle_on_both_change_their_mind(self) -> None:
        """Галка «не считать дорожку» возвращает серию в работу."""
        from app.services.autodownload import missing_episodes
        from app.services.autodownload_rules import Resolved

        _seed(ERAI_STREAMS)
        rule = Resolved(lang="ru", priorities=["crunchyroll"], ignore_embedded=True)

        assert missing_episodes(SERIES, rule) == [1.0]
        # Страница при этом по-прежнему честно говорит, что дорожка есть:
        # это факт о файле, а не о настройке.
        assert "ru" in _row()["embedded_subs"]
