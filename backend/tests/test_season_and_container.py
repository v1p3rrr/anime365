"""Сезон, названный только именами файлов, и папка-контейнер с лишним файлом.

Оба случая взяты с живой библиотеки.

* `Classroom of the Elite` — 17 файлов `S04E01…` прямо в папке тайтла, подпапок
  сезонов нет вовсе. Ни `season.nfo`, ни имя папки о четвёртом сезоне не
  говорят, и папка уезжала на первый сезон.
* `Toradora!` — в корне один файл (спешл, попавший туда при скачивании), в
  `Season 1` двадцать девять. Правило «корень без своих видео» не срабатывало
  из-за этого одного файла, и тайтл оставался привязан дважды.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Series
from app.models.library import LibraryFolder, LibraryRoot, TitleMapping
from app.services.filename_rules import get_rules, parse_filename
from app.services.library_service import _drop_container_duplicates
from app.services.matcher import MatchContext


class TestSeasonFromFiles:
    def test_real_file_names_carry_the_season(self) -> None:
        """Номер сезона в этих именах есть — его и не хватало разбору папки."""
        parsed = parse_filename(
            "Classroom of the Elite S04E01 - Assassin From the White Room "
            "[Erai-raws] WEBRip-1080p.mkv",
            "series",
            get_rules(),
        )

        assert parsed.season == 4
        assert parsed.episode == 1

    def test_unanimous_file_season_becomes_the_folder_season(self) -> None:
        ctx = MatchContext(
            folder_name="Classroom of the Elite",
            scope="series",
            season_from_files=4,
        )

        assert ctx.season_number == 4
        assert ctx.season_is_guessed is True

    def test_metadata_wins_over_guess(self) -> None:
        """У `season.nfo` номер настоящий — догадка его не перебивает."""
        from app.services.nfo_parser import NfoData

        ctx = MatchContext(
            folder_name="Yuru Yuri",
            scope="series",
            season_nfo=NfoData(kind="season", path="", season=2),
            season_from_files=4,
        )

        assert ctx.season_number == 2
        assert ctx.season_is_guessed is False

    def test_folder_name_season_wins_over_guess(self) -> None:
        ctx = MatchContext(
            folder_name="Yuru Camp",
            scope="series",
            own_season=2,
            season_from_files=4,
        )

        assert ctx.season_number == 2
        assert ctx.season_is_guessed is False

    def test_no_season_at_all(self) -> None:
        assert MatchContext(folder_name="Frieren", scope="series").season_number is None


class TestContainerDuplicates:
    @staticmethod
    def _build(tmp_path: Path, root_videos: int, child_videos: int) -> tuple[int, int, int]:
        """Папка тайтла с подпапкой сезона. Возвращает id корня, папки и потомка."""
        series_id = 980001
        with session_scope() as session:
            if session.get(Series, series_id) is None:
                session.add(Series(id=series_id, title="Toradora!", type="tv"))
            root = LibraryRoot(path=str(tmp_path), kind="series")
            session.add(root)
            session.flush()

            parent = LibraryFolder(
                root_id=root.id, rel_path="Toradora!", name="Toradora!", video_count=root_videos
            )
            session.add(parent)
            session.flush()
            child = LibraryFolder(
                root_id=root.id,
                rel_path="Toradora!/Season 1",
                name="Season 1",
                parent_id=parent.id,
                video_count=child_videos,
            )
            session.add(child)
            session.flush()

            session.execute(delete(TitleMapping).where(TitleMapping.series_id == series_id))
            for folder in (parent, child):
                session.add(
                    TitleMapping(
                        series_id=series_id,
                        folder_id=folder.id,
                        source="exact_title",
                        confidence=1.0,
                    )
                )
            return root.id, parent.id, child.id

    @staticmethod
    def _run(root_id: int) -> tuple[int, set[int]]:
        with session_scope() as session:
            folders = (
                session.execute(select(LibraryFolder).where(LibraryFolder.root_id == root_id))
                .scalars()
                .all()
            )
            dropped = _drop_container_duplicates(session, folders)
            session.flush()
            left = {
                folder_id
                for (folder_id,) in session.execute(
                    select(TitleMapping.folder_id).where(
                        TitleMapping.folder_id.in_([f.id for f in folders])
                    )
                ).all()
            }
        return dropped, left

    def test_one_stray_file_no_longer_keeps_the_duplicate(self, tmp_path: Path) -> None:
        root_id, parent_id, child_id = self._build(tmp_path, root_videos=1, child_videos=29)

        dropped, left = self._run(root_id)

        assert dropped == 1
        assert left == {child_id}, "привязка обязана остаться у папки с сериями"

    def test_empty_root_still_yields(self, tmp_path: Path) -> None:
        """Прежний случай продолжает работать."""
        root_id, parent_id, child_id = self._build(tmp_path, root_videos=0, child_videos=12)

        dropped, left = self._run(root_id)

        assert dropped == 1
        assert left == {child_id}

    def test_working_folder_is_not_dropped(self, tmp_path: Path) -> None:
        """Серии лежат в корне, а в подпапке пара бонусов — корень не контейнер."""
        root_id, parent_id, child_id = self._build(tmp_path, root_videos=26, child_videos=2)

        dropped, left = self._run(root_id)

        assert dropped == 0
        assert left == {parent_id, child_id}
