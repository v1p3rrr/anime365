"""У онгоинга «вышло» и «запланировано» — разные числа.

Просьба пользователя: на карточке должно читаться «10/11(18)» — скачано,
вышло, всего по плану. До этого знаменатель был один: максимум из плана,
серий в API и локального номера. У Re:Zero 4th Season план 19 при 11
выложенных сериях, и полностью скачанный онгоинг показывался как «11/19»
красным — будто половины не хватает, хотя качать больше нечего.

Правило «локально серий больше, чем знает сайт» при этом сохраняется: оно
относится именно к вышедшему, иначе получится «25/22».
"""

from __future__ import annotations

from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Episode, Series
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, LibraryRoot
from app.services.mapping_info import series_badges

ROOT = "/test/ongoing-badge"


def _seed(series_id: int, planned: int, aired: int, downloaded: list[float]) -> None:
    """Тайтл с планом `planned`, `aired` выложенными сериями и файлами на диске."""
    with session_scope() as session:
        session.merge(
            Series(
                id=series_id,
                title=f"Ongoing {series_id}",
                type="tv",
                number_of_episodes=planned,
            )
        )
        session.execute(delete(Episode).where(Episode.series_id == series_id))
        session.execute(delete(EpisodeMapping).where(EpisodeMapping.series_id == series_id))
        session.flush()

        for num in range(1, aired + 1):
            session.add(
                Episode(
                    id=series_id * 100 + num,
                    series_id=series_id,
                    episode_num=str(num),
                    episode_type="tv",
                )
            )

        root = session.execute(
            select(LibraryRoot).where(LibraryRoot.path == ROOT)
        ).scalar_one_or_none()
        if root is None:
            root = LibraryRoot(path=ROOT, kind="series")
            session.add(root)
            session.flush()

        name = f"Ongoing {series_id}"
        folder = session.execute(
            select(LibraryFolder).where(
                LibraryFolder.root_id == root.id, LibraryFolder.rel_path == name
            )
        ).scalar_one_or_none()
        if folder is None:
            folder = LibraryFolder(root_id=root.id, rel_path=name, name=name)
            session.add(folder)
        folder.video_count = len(downloaded)
        session.flush()

        for num in downloaded:
            video = LibraryFile(
                folder_id=folder.id,
                filename=f"{name} S01E{int(num):02d}.mkv",
                kind="video",
                size_bytes=1024,
                mtime=1.0,
                episode=num,
                matched_rule_id="jellyfin_sxxexx",
            )
            session.add(video)
            session.flush()
            session.add(
                EpisodeMapping(
                    series_id=series_id, episode=num, file_id=video.id, role="video"
                )
            )


class TestOngoingCounts:
    def test_aired_is_below_the_plan(self) -> None:
        """Тот самый случай: 10 скачано, 11 выложено, 19 запланировано."""
        series_id = 990_101
        _seed(series_id, planned=19, aired=11, downloaded=[float(n) for n in range(1, 11)])

        badge = series_badges([series_id])[series_id]

        assert badge["video"] == 10
        assert badge["aired"] == 11
        assert badge["total"] == 19

    def test_finished_title_has_no_gap(self) -> None:
        """У завершённого план и выложенное совпадают — скобок в UI не будет."""
        series_id = 990_102
        _seed(series_id, planned=12, aired=12, downloaded=[1.0])

        badge = series_badges([series_id])[series_id]

        assert badge["aired"] == badge["total"] == 12

    def test_local_files_beyond_the_api_raise_aired(self) -> None:
        """Локально серий больше, чем знает сайт: растёт именно «вышло»."""
        series_id = 990_103
        _seed(series_id, planned=12, aired=12, downloaded=[25.0])

        badge = series_badges([series_id])[series_id]

        assert badge["aired"] == 25
        assert badge["total"] == 25

    def test_plan_never_shrinks_the_denominator(self) -> None:
        """План меньше выложенного — знаменатель остаётся по выложенному.

        Сайт иногда занижает `numberOfEpisodes` у давних тайтлов; «12/8»
        было бы хуже, чем просто «12/12».
        """
        series_id = 990_104
        _seed(series_id, planned=8, aired=12, downloaded=[1.0])

        badge = series_badges([series_id])[series_id]

        assert badge["aired"] == 12
        assert badge["total"] == 12
