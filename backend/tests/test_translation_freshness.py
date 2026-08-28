"""Свежесть кэша переводов не должна быть недостижимой.

⚠️ Найдено пользователем на живом сервере, тайтл 40024: страница снова
показывала «подгружаются с Anime365…» без конца, а в сеть уходил запрос за
запросом. Прежний сторож (`test_translation_refresh_backoff.py`) закрывал петлю
через **неудачу**; здесь петля собиралась из одних успехов.

Устройство было такое:

1. свежесть тайтла считается как `min(synced_at)` по всем его переводам;
2. перевод, исчезнувший с сайта, но привязанный к скачанному файлу, **не
   удаляется**, а помечается ([D-173](../../claude/09-decisions.md)) — и в
   ответе Anime365 его больше нет, значит `synced_at` ему никто не обновляет;
3. минимум навсегда остаётся старым → тайтл вечно «несвежий» → каждый запрос
   страницы заводит фоновую догрузку → клиент видит `translations_pending` и
   опрашивает раз в полторы секунды → и так по кругу.

Ни одна из трёх частей по отдельности не выглядит ошибкой. Поэтому тест сторожит
именно стык: **после успешного похода тайтл обязан стать свежим**, что бы с его
записями ни случилось.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Episode, Series, Translation
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, LibraryRoot
from app.services import title_service
from app.services.title_service import (
    TRANSLATIONS_TTL,
    _reconcile_with_site,
    should_recheck_episode_list,
    translations_are_fresh,
)

SERIES = 980_071
KEPT = 771_001
STILL_THERE = 771_002
ROOT = "/test/freshness"


@pytest.fixture(autouse=True)
def clean():
    def wipe() -> None:
        title_service._local_max_checked.pop(SERIES, None)
        with session_scope() as session:
            session.execute(delete(EpisodeMapping).where(EpisodeMapping.series_id == SERIES))
            session.execute(delete(Translation).where(Translation.series_id == SERIES))
            session.execute(delete(Episode).where(Episode.series_id == SERIES))
            session.execute(delete(Series).where(Series.id == SERIES))
            root = session.execute(
                select(LibraryRoot).where(LibraryRoot.path == ROOT)
            ).scalar_one_or_none()
            if root is not None:
                for folder in session.execute(
                    select(LibraryFolder).where(LibraryFolder.root_id == root.id)
                ).scalars():
                    session.execute(delete(LibraryFile).where(LibraryFile.folder_id == folder.id))
                session.execute(delete(LibraryFolder).where(LibraryFolder.root_id == root.id))
                session.delete(root)

    wipe()
    yield
    wipe()


def _seed_with_a_downloaded_translation() -> None:
    """Тайтл, у которого один перевод скачан, а второй просто есть."""
    just_now = datetime.utcnow()
    with session_scope() as session:
        session.merge(Series(id=SERIES, title="Freshness", type="tv", number_of_episodes=1))
        for translation_id in (KEPT, STILL_THERE):
            session.merge(
                Translation(
                    id=translation_id,
                    series_id=SERIES,
                    type_kind="sub",
                    type_lang="ru",
                    # Обе записи только что синхронизированы: важен не возраст,
                    # а то, обновится ли он у исчезнувшей.
                    synced_at=just_now,
                )
            )
        root = LibraryRoot(path=ROOT, kind="series")
        session.add(root)
        session.flush()
        folder = LibraryFolder(root_id=root.id, rel_path="Fresh", name="Fresh")
        session.add(folder)
        session.flush()
        session.add(
            LibraryFile(
                folder_id=folder.id,
                filename=f"Fresh S01E01.anime365_t{KEPT}.ru.ass",
                kind="subtitle",
                size_bytes=10,
                mtime=1.0,
                episode=1.0,
                # Связь идёт через `t{id}` в имени: только по нему видно, что
                # запись бросать нельзя.
                anime365_translation_id=KEPT,
            )
        )


def _age(translation_id: int, delta: timedelta) -> None:
    with session_scope() as session:
        session.get(Translation, translation_id).synced_at = datetime.utcnow() - delta


class TestAVanishedButKeptTranslationDoesNotFreezeFreshness:
    def test_it_survives_the_reconcile(self) -> None:
        """Сперва убеждаемся, что запись действительно остаётся с пометкой."""
        _seed_with_a_downloaded_translation()

        _reconcile_with_site(SERIES, present_translations={STILL_THERE})

        with session_scope() as session:
            kept = session.get(Translation, KEPT)
            assert kept is not None, "к ней привязан файл — удалять нельзя"
            assert kept.missing_since is not None

    def test_and_counts_as_checked(self) -> None:
        """Главное. Проверили — значит проверили, пусть ответ и «её больше нет».

        Без этого минимум по тайтлу оставался старым навсегда, и страница
        уходила в вечный опрос.
        """
        _seed_with_a_downloaded_translation()
        _age(KEPT, TRANSLATIONS_TTL + timedelta(hours=1))
        assert not translations_are_fresh(SERIES), "исходное состояние — несвежо"

        _reconcile_with_site(SERIES, present_translations={STILL_THERE})

        assert translations_are_fresh(SERIES), (
            "после похода на сайт тайтл обязан стать свежим, иначе страница "
            "заводит догрузку на каждый запрос"
        )

    def test_an_empty_answer_still_proves_nothing(self) -> None:
        """Пустой список — не доказательство удаления, и отметок он не ставит."""
        _seed_with_a_downloaded_translation()
        _age(KEPT, TRANSLATIONS_TTL + timedelta(hours=1))

        _reconcile_with_site(SERIES, present_translations=set())

        with session_scope() as session:
            assert session.get(Translation, KEPT).missing_since is None
        assert not translations_are_fresh(SERIES)


class TestALocalFileCanOutrunTheEpisodeList:
    """Список серий обновляется только вместе с переводами, то есть раз в шесть
    часов и только при заходе на страницу. У онгоинга файл новой серии
    появляется раньше — и строки в таблице для него нет.
    """

    @staticmethod
    def _seed_episodes(known: list[float], local: list[float]) -> None:
        with session_scope() as session:
            session.merge(Series(id=SERIES, title="Ongoing", type="tv", number_of_episodes=12))
            for number in known:
                session.merge(
                    Episode(
                        id=int(900_000 + number),
                        series_id=SERIES,
                        episode_int=str(int(number)),
                        episode_num=number,
                        episode_type="tv",
                        is_active=True,
                    )
                )
            root = LibraryRoot(path=ROOT, kind="series")
            session.add(root)
            session.flush()
            folder = LibraryFolder(root_id=root.id, rel_path="Ongoing", name="Ongoing")
            session.add(folder)
            session.flush()
            for number in local:
                video = LibraryFile(
                    folder_id=folder.id,
                    filename=f"Ongoing S01E{int(number):02d}.mkv",
                    kind="video",
                    size_bytes=100,
                    mtime=1.0,
                    episode=number,
                )
                session.add(video)
                session.flush()
                session.add(
                    EpisodeMapping(
                        series_id=SERIES, episode=number, file_id=video.id, role="video"
                    )
                )

    def test_a_file_beyond_the_list_asks_for_a_recheck(self) -> None:
        self._seed_episodes(known=[1.0, 2.0], local=[1.0, 2.0, 3.0])

        assert should_recheck_episode_list(SERIES) is True

    def test_but_only_once_for_the_same_number(self) -> None:
        """Локальных серий законно бывает больше, чем знает сайт.

        Без памяти тот же сигнал стал бы вечным походом в сеть — ровно та петля,
        от которой лечит первая половина этого файла.
        """
        self._seed_episodes(known=[1.0, 2.0], local=[1.0, 2.0, 3.0])

        assert should_recheck_episode_list(SERIES) is True
        assert should_recheck_episode_list(SERIES) is False

    def test_a_list_that_knows_everything_asks_for_nothing(self) -> None:
        self._seed_episodes(known=[1.0, 2.0, 3.0], local=[1.0, 2.0, 3.0])

        assert should_recheck_episode_list(SERIES) is False

    def test_without_local_files_there_is_no_signal(self) -> None:
        self._seed_episodes(known=[1.0], local=[])

        assert should_recheck_episode_list(SERIES) is False
