"""Файл со своим `t{id}` опознаётся сам — даже когда рядом лежат его соседи.

Продолжение `test_junk_folder_files.py`, и разница между ними — вся суть.
Там папка `Bang Dream!` собрана из одних `.mkv`. На сервере рядом с каждым
видео лежит то, что положил Jellyfin: `.nfo`, обложки, а рядом с ними наши же
субтитры. Имя у всех одно и то же, значит и `t{id}` в них один и тот же:

    [Anime365] … - 01 (t3950602)-poster.jpg
    [Anime365] … - 01 (t3950602).ja.srt
    [Anime365] … - 01 (t3950602).mkv
    [Anime365] … - 01 (t3950602).nfo

Имена настоящие, скопированы с сервера. Там таких групп девять, самая большая —
из семи файлов.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Translation
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, TitleMapping
from app.services.library_scan import scan_root
from app.services.library_service import _map_episodes, _persist_root

from tests.test_junk_folder_files import (
    FOLDER_SERIES,
    POPPIN_DREAM,
    ROSELIA_I,
    _seed_catalog,
)

POPPIN = "[Anime365] BanG Dream Movie Poppin Dream - 01 (t4000194)"
ROSELIA = "[Anime365] BanG Dream Movie Episode of Roselia - I Yakusoku - 01 (t3670469)"

#: Ровно то, что лежит в папке на сервере.
NEIGHBOURS = ("-backdrop.jpg", "-logo.png", "-poster.jpg", ".ja.srt", ".mkv", ".nfo")


def _build(tmp_path: Path, name: str) -> int:
    _seed_catalog()
    root = tmp_path / name
    folder = root / "Bang Dream!"
    folder.mkdir(parents=True)
    for stem in (POPPIN, ROSELIA):
        for suffix in NEIGHBOURS:
            (folder / f"{stem}{suffix}").write_bytes(b"x" * 10)

    root_id = _persist_root(scan_root(str(root), "movie"))
    with session_scope() as session:
        folder_id = session.execute(
            select(LibraryFolder.id).where(LibraryFolder.root_id == root_id)
        ).scalar_one()
        session.execute(delete(TitleMapping).where(TitleMapping.folder_id == folder_id))
        session.add(
            TitleMapping(
                series_id=FOLDER_SERIES,
                folder_id=folder_id,
                source="exact_title",
                confidence=1.0,
            )
        )
    _map_episodes(root_id)
    return root_id


def _linked(root_id: int, series_id: int) -> set[str]:
    # ⚠️ Сужается до своего корня: база у прогона одна, а имена файлов
    # здесь совпадают с соседними тестами до буквы.
    with session_scope() as session:
        return {
            name
            for (name,) in session.execute(
                select(LibraryFile.filename)
                .join(EpisodeMapping, EpisodeMapping.file_id == LibraryFile.id)
                .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
                .where(
                    EpisodeMapping.series_id == series_id,
                    LibraryFolder.root_id == root_id,
                )
            ).all()
        }


def _file(root_id: int, filename: str) -> LibraryFile:
    with session_scope() as session:
        return session.execute(
            select(LibraryFile)
            .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
            .where(LibraryFile.filename == filename, LibraryFolder.root_id == root_id)
        ).scalar_one()


class TestNeighboursDoNotStealTheIdentifier:
    def test_video_goes_to_its_own_title(self, tmp_path: Path) -> None:
        # ⚠️ Здесь и ломалось: соседей больше, чем видео, и `.nfo` шёл в списке
        # последним. Идентификатор доставался ему, а видео уезжало к тайтлу
        # папки — то есть ровно туда, откуда `t{id}` его и должен был забрать.
        root_id = _build(tmp_path, "neighbours")
        assert POPPIN + ".mkv" in _linked(root_id, POPPIN_DREAM)
        assert ROSELIA + ".mkv" in _linked(root_id, ROSELIA_I)

    def test_subtitles_go_with_their_video(self, tmp_path: Path) -> None:
        root_id = _build(tmp_path, "neighbours-subs")
        assert POPPIN + ".ja.srt" in _linked(root_id, POPPIN_DREAM)
        assert ROSELIA + ".ja.srt" in _linked(root_id, ROSELIA_I)

    def test_nothing_lands_on_the_folder_title(self, tmp_path: Path) -> None:
        # У папки свой тайтл есть, но ни один из этих файлов ему не принадлежит.
        root_id = _build(tmp_path, "neighbours-folder")
        assert _linked(root_id, FOLDER_SERIES) == set()

    def test_artwork_and_nfo_are_not_linked(self, tmp_path: Path) -> None:
        # Обратная сторона: раздать идентификатор всем — не то же самое, что
        # связать всех. Обложки и `.nfo` ролью не обладают и связей не получают.
        root_id = _build(tmp_path, "neighbours-artwork")
        linked = (
            _linked(root_id, POPPIN_DREAM)
            | _linked(root_id, ROSELIA_I)
            | _linked(root_id, FOLDER_SERIES)
        )
        assert not [name for name in linked if name.endswith((".jpg", ".png", ".nfo"))]


class TestManualLanguageIsKept:
    """Выставленный руками язык субтитров не переписывается связкой серий.

    Скан это правило соблюдает (`lang_is_manual` в `_persist_folder`), а связка
    серий подписывала язык по `type_lang` перевода, ничего не спрашивая: у файла
    со своим `t{id}` ручная пометка держалась ровно до следующего прохода.
    """

    def test_manual_language_survives_a_remap(self, tmp_path: Path) -> None:
        root_id = _build(tmp_path, "manual-lang")

        file_id = _file(root_id, POPPIN + ".ja.srt").id
        with session_scope() as session:
            row = session.get(LibraryFile, file_id)
            row.lang = "ru"
            row.lang_is_manual = True

        _map_episodes(root_id)

        with session_scope() as session:
            assert (
                session.get(LibraryFile, file_id).lang == "ru"
            ), "связка серий переписала ручной язык"

            # ⚠️ И роль обязана следовать ручному языку, а не языку перевода.
            # Правка выносила `role = SUB_ROLE[...]` внутрь ветки, и это было
            # сделано намеренно — но проверено рассуждением, а не прогоном.
            roles = {
                row[0]
                for row in session.execute(
                    select(EpisodeMapping.role).where(EpisodeMapping.file_id == file_id)
                ).all()
            }
            assert roles == {"sub_ru"}, f"роль не пошла за ручным языком: {roles}"

    def test_automatic_language_is_still_corrected(self, tmp_path: Path) -> None:
        # Обратная сторона: без ручной пометки подпись по переводу нужна — ради
        # неё правило и заводилось.
        root_id = _build(tmp_path, "auto-lang")

        file_id = _file(root_id, ROSELIA + ".ja.srt").id
        with session_scope() as session:
            # Язык берётся у перевода, а не из имени файла, — значит у
            # перевода он должен быть проставлен.
            session.get(Translation, 3670469).type_lang = "ja"
            row = session.get(LibraryFile, file_id)
            row.lang = None
            row.lang_is_manual = False

        _map_episodes(root_id)

        with session_scope() as session:
            assert session.get(LibraryFile, file_id).lang == "ja"
