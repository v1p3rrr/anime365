"""Окно «что лежит за этой серией»: путь каждого файла и его дорожки.

Значки наличия в строке серии отвечают «есть», но не отвечают «что именно» —
а спрашивают об этом ровно тогда, когда файл смапился не туда. Поэтому главное
здесь — **полный путь**: по нему файл ищут руками.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, LibraryRoot

SERIES_ID = 990001
ROOT = "/test/episode-files"
FOLDER = "Chainsaw Man - The Movie - Reze Arc"
VIDEO = "Chainsaw Man - The Movie - Reze Arc (2025) - [WEBDL-1080p] [JA].mkv"
SUBTITLE = "Chainsaw Man - The Movie - Reze Arc (2025) - [WEBDL-1080p] [JA].ja.srt"


def _seed() -> None:
    with session_scope() as session:
        if session.get(Series, SERIES_ID) is None:
            session.add(Series(id=SERIES_ID, title="Chainsaw Man: Reze-hen", type="movie"))

        root = session.execute(
            select(LibraryRoot).where(LibraryRoot.path == ROOT)
        ).scalar_one_or_none()
        if root is None:
            root = LibraryRoot(path=ROOT, kind="movie")
            session.add(root)
            session.flush()

        session.execute(delete(LibraryFolder).where(LibraryFolder.root_id == root.id))
        session.execute(delete(EpisodeMapping).where(EpisodeMapping.series_id == SERIES_ID))
        session.flush()

        folder = LibraryFolder(root_id=root.id, rel_path=FOLDER, name=FOLDER, video_count=1)
        session.add(folder)
        session.flush()

        video = LibraryFile(
            folder_id=folder.id,
            filename=VIDEO,
            kind="video",
            size_bytes=1024,
            mtime=1.0,
            episode=1,
            matched_rule_id="single_movie",
            streams={"audio_languages": ["jpn"], "subtitle_languages": []},
        )
        subtitle = LibraryFile(
            folder_id=folder.id,
            filename=SUBTITLE,
            kind="subtitle",
            lang="ja",
            size_bytes=64,
            mtime=1.0,
            episode=1,
            matched_rule_id="sidecar_of_video",
        )
        session.add_all([video, subtitle])
        session.flush()
        session.add_all(
            [
                EpisodeMapping(
                    series_id=SERIES_ID, episode=1, file_id=video.id, role="video"
                ),
                EpisodeMapping(
                    series_id=SERIES_ID,
                    episode=1,
                    file_id=subtitle.id,
                    role="sub_ja",
                    is_manual=True,
                ),
            ]
        )


def _files(client, episode: float = 1) -> list[dict]:
    response = client.get(f"/api/library/series/{SERIES_ID}/episodes/{episode}/files")
    assert response.status_code == 200, response.text
    return response.json()


class TestEpisodeFiles:
    def test_returns_both_roles(self, client) -> None:
        _seed()
        roles = sorted(f["role"] for f in _files(client))

        assert roles == ["sub_ja", "video"]

    def test_path_is_whole_and_absolute(self, client) -> None:
        """Разложенный по колонкам путь бесполезен — человеку нужен целиком."""
        _seed()
        video = next(f for f in _files(client) if f["role"] == "video")

        assert PurePosixPath(video["path"].replace("\\", "/")).as_posix() == (
            f"{ROOT}/{FOLDER}/{VIDEO}"
        )

    def test_tracks_and_provenance_are_visible(self, client) -> None:
        _seed()
        files = {f["role"]: f for f in _files(client)}

        assert files["video"]["streams"]["audio_languages"] == ["jpn"]
        assert files["video"]["matched_rule_id"] == "single_movie"
        assert files["sub_ja"]["matched_rule_id"] == "sidecar_of_video"
        assert files["sub_ja"]["is_manual"] is True

    def test_other_episode_is_empty(self, client) -> None:
        _seed()
        assert _files(client, episode=2) == []


class TestProbeOneFile:
    def test_disabled_probe_says_so(self, client) -> None:
        """Отказ должен быть внятным: тумблер выключен — это не поломка."""
        from app.core.config import settings

        _seed()
        with session_scope() as session:
            file_id = session.execute(
                select(LibraryFile.id).where(LibraryFile.filename == VIDEO)
            ).scalar_one()

        settings.set_db_override("enable_ffprobe", "false")
        try:
            response = client.post(f"/api/library/files/{file_id}/probe")
        finally:
            settings.set_db_override("enable_ffprobe", None)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "ffprobe_disabled"

    def test_missing_file_is_404(self, client) -> None:
        from app.core.config import settings

        settings.set_db_override("enable_ffprobe", "true")
        try:
            response = client.post("/api/library/files/99999999/probe")
        finally:
            settings.set_db_override("enable_ffprobe", None)

        # 404 — файла нет; 503 — ffmpeg не установлен на машине разработчика.
        assert response.status_code in (404, 503)
