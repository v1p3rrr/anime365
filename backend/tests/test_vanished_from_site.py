"""Исчезнувшее с сайта убирается, но не вместе со скачанным.

Живой случай: у тайтла 40024 Anime365 удалил ONA-нарезку после выхода седьмой
серии. У нас она осталась в кэше навсегда — страница продолжала её показывать и
предлагать к скачиванию, хотя ссылки уже не существовало.

⚠️ **Определить удаление запросом ссылки нельзя.** Проверено на живом сайте:
адрес удалённой серии отдаёт не 404, а цепочку редиректов `302 → 302 → 301` и
код **200** — на странице тайтла. Живая серия при этом даёт один `301` и
попадает ровно на страницу перевода. Поэтому единственный признак — отсутствие
записи в списке, который сайт отдаёт целиком.

Правило пользователя: чего не жалко — удаляем; к чему привязан скачанный файл —
оставляем с пометкой, скачивать больше не предлагаем, а файл продолжаем
показывать. Второе не роскошь: `t{id}` из имени файла ведёт именно в эти строки,
и без них скачанное осталось бы без автора, языка и серии.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Episode, Series, Translation
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot
from app.services.autodownload import _candidates
from app.services.title_service import _reconcile_with_site, build_title_page

SERIES = 980_042
TV_ONE = 371_590
ONA_ONE = 380_228

ROOT = "/tmp/a365-vanished-tests"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(
        "app.services.title_service.schedule_translation_refresh", lambda series_id: False
    )

    def wipe() -> None:
        with session_scope() as session:
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


def _title() -> None:
    with session_scope() as session:
        session.merge(
            Series(id=SERIES, title="Vanishing", type="tv", has_details=True)
        )


def _episode(episode_id: int, number: float, episode_type: str) -> None:
    with session_scope() as session:
        session.merge(
            Episode(
                id=episode_id,
                series_id=SERIES,
                episode_int=str(int(number)),
                episode_num=number,
                episode_type=episode_type,
                episode_full=f"{episode_type} {int(number)}",
                is_active=True,
            )
        )


def _translation(tid: int, episode_id: int, url: str = "https://example.invalid/x") -> None:
    with session_scope() as session:
        session.merge(
            Translation(
                id=tid,
                series_id=SERIES,
                episode_id=episode_id,
                type_kind="sub",
                type_lang="ru",
                authors_summary="Ironclad",
                url=url,
                is_active=True,
            )
        )


def _downloaded(translation_id: int) -> None:
    """Скачанный файл, привязанный к переводу через `t{id}` в имени."""
    with session_scope() as session:
        root = session.execute(
            select(LibraryRoot).where(LibraryRoot.path == ROOT)
        ).scalar_one_or_none()
        if root is None:
            root = LibraryRoot(path=ROOT, kind="series")
            session.add(root)
            session.flush()
        folder = LibraryFolder(root_id=root.id, rel_path="Vanishing", name="Vanishing")
        session.add(folder)
        session.flush()
        session.add(
            LibraryFile(
                folder_id=folder.id,
                filename=f"Vanishing S01E01 [anime365_Ironclad_t{translation_id}].ru.ass",
                kind="subtitle",
                size_bytes=1,
                mtime=1.0,
                episode=1.0,
                anime365_translation_id=translation_id,
            )
        )


def _rows(model):
    with session_scope() as session:
        return list(
            session.execute(select(model).where(model.series_id == SERIES)).scalars()
        )


class TestNothingLocalMeansDelete:
    def test_a_vanished_translation_goes(self):
        _title()
        _episode(TV_ONE, 1, "tv")
        _translation(1, TV_ONE)
        _translation(2, TV_ONE)

        stats = _reconcile_with_site(SERIES, present_translations={1})

        assert stats["translations_deleted"] == 1
        assert [row.id for row in _rows(Translation)] == [1]

    def test_a_vanished_episode_goes(self):
        """Ровно случай 40024: ONA-нарезку удалили, файлов по ней нет."""
        _title()
        _episode(TV_ONE, 1, "tv")
        _episode(ONA_ONE, 1, "ona")

        stats = _reconcile_with_site(SERIES, present_episodes={TV_ONE})

        assert stats["episodes_deleted"] == 1
        assert [row.id for row in _rows(Episode)] == [TV_ONE]


class TestDownloadedIsNeverLost:
    def test_a_translation_with_a_file_is_only_marked(self):
        _title()
        _episode(TV_ONE, 1, "tv")
        _translation(1, TV_ONE)
        _downloaded(1)

        stats = _reconcile_with_site(SERIES, present_translations={999})

        assert stats["translations_deleted"] == 0
        assert stats["translations_marked"] == 1
        assert _rows(Translation)[0].missing_since is not None

    def test_an_episode_with_a_downloaded_translation_is_only_marked(self):
        _title()
        _episode(ONA_ONE, 1, "ona")
        _translation(1, ONA_ONE)
        _downloaded(1)

        stats = _reconcile_with_site(SERIES, present_episodes={TV_ONE})

        assert stats["episodes_deleted"] == 0
        assert stats["episodes_marked"] == 1
        assert _rows(Episode)[0].missing_since is not None

    def test_marking_twice_does_not_double_count(self):
        _title()
        _episode(TV_ONE, 1, "tv")
        _translation(1, TV_ONE)
        _downloaded(1)

        _reconcile_with_site(SERIES, present_translations={999})
        again = _reconcile_with_site(SERIES, present_translations={999})

        assert again["translations_marked"] == 0


class TestAnEmptyAnswerIsNotProof:
    """Сеть отвалилась, токен протух, сайт отдал пустую страницу.

    Ровно на этих граблях уже стоял обход библиотеки: «отсутствует в
    результате» там означает «нет на диске», и частичный ответ снёс бы всё.
    """

    def test_an_empty_translation_list_wipes_nothing(self):
        _title()
        _episode(TV_ONE, 1, "tv")
        _translation(1, TV_ONE)

        stats = _reconcile_with_site(SERIES, present_translations=set())

        assert stats["translations_deleted"] == 0
        assert len(_rows(Translation)) == 1

    def test_an_empty_episode_list_wipes_nothing(self):
        _title()
        _episode(TV_ONE, 1, "tv")

        stats = _reconcile_with_site(SERIES, present_episodes=set())

        assert stats["episodes_deleted"] == 0
        assert len(_rows(Episode)) == 1


class TestComingBackClearsTheMark:
    def test_a_returned_translation_is_normal_again(self):
        _title()
        _episode(TV_ONE, 1, "tv")
        _translation(1, TV_ONE)
        _downloaded(1)
        _reconcile_with_site(SERIES, present_translations={999})

        stats = _reconcile_with_site(SERIES, present_translations={1})

        assert stats["restored"] == 1
        assert _rows(Translation)[0].missing_since is None


class TestTheMarkedOneIsNotOffered:
    def test_it_disappears_from_the_download_choice(self):
        _title()
        _episode(TV_ONE, 1, "tv")
        _translation(1, TV_ONE)
        _translation(2, TV_ONE)
        _downloaded(1)
        _reconcile_with_site(SERIES, present_translations={2})

        page = asyncio.run(build_title_page(SERIES))
        row = [e for e in page["episodes"] if e["id"] == TV_ONE][0]

        assert [t["id"] for t in row["translations"]] == [2]

    def test_autodownload_does_not_take_it(self):
        _title()
        _episode(TV_ONE, 1, "tv")
        _translation(1, TV_ONE)
        _downloaded(1)
        _reconcile_with_site(SERIES, present_translations={999})

        assert _candidates(SERIES, "ru") == {}

    def test_a_vanished_episode_loses_its_link(self):
        """Ссылка уводит на страницу тайтла, а не на серию: проверено запросом."""
        _title()
        _episode(ONA_ONE, 1, "ona")
        _translation(1, ONA_ONE, url="https://smotret-anime.org/catalog/x/1-seriya-380228/y")
        _downloaded(1)
        _reconcile_with_site(SERIES, present_episodes={TV_ONE})

        page = asyncio.run(build_title_page(SERIES))
        row = [e for e in page["episodes"] if e["id"] == ONA_ONE][0]

        assert row["missing"] is True
        assert row["url"] is None
