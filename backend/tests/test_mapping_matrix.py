"""Матрица раскладок: папка на диске → тайтл и серии.

Прогон идёт **через весь конвейер** — обход диска, запись в базу, маппинг папок,
связывание серий, — а не через матчер в отдельности. Ошибки этой сессии почти
все жили на стыках: разбор был верен, а до него не доходило; правило было, а
папку не перечитывали.

Оси матрицы, каждая взята из настоящей библиотеки:

* **корень** — сериалы или фильмы;
* **уровень папки** — тайтл, сезон, спешлы, релизная подпапка, служебная;
* **откуда название** — свой `.nfo`, родительский, только имя папки, `.nfo` о
  другом тайтле, `.nfo` нет вовсе;
* **откуда номер серии** — `SxxExx`, эпизодный `.nfo`, `t{id}`, номер сезона в
  именах файлов, единственное видео фильма;
* **сколько видео** — ноль, одно, несколько.

Каждый тест ниже — клетка этой матрицы; в имени класса записано, какая.

⚠️ Карта покрытия — в `claude/15-mapping-matrix.md`: там перечислено и то, что
**не** покрыто. Добавляя клетку сюда, отметьте её там.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select

from app.core.db import Base, engine, session_scope
from app.models.catalog import Episode, Series, Translation
from app.models.library import EpisodeMapping, LibraryFile, LibraryFolder, TitleMapping
from app.services.library_scan import scan_root
from app.services.mapping_info import ensure_primary_mapping
from app.services.library_service import (
    _map_episodes,
    _map_folders,
    _persist_root,
    build_index,
)

# --- каталог -------------------------------------------------------------- #
# Идентификаторы из своего диапазона, чтобы не пересекаться с другими тестами.
SHOW = 900101  # сериал, первый сезон
SHOW_S2 = 900102  # его второй сезон, номер назван в заголовке
SPINOFF = 900103  # чиби-спиноф того же квартала, что и второй сезон
SPECIAL = 900104  # спешл франшизы
MOVIE = 900105  # одиночный фильм
OTHER_MOVIE = 900106  # посторонний фильм с похожим названием, другой год

CATALOG = [
    (SHOW, "Kagerou Days", "tv", 2019, "Зима 2019", 13, 700001),
    (SHOW_S2, "Kagerou Days 2nd Season", "tv", 2021, "Зима 2021", 13, 700002),
    (SPINOFF, "Kagerou Days: Pico", "ona", 2021, "Зима 2021", 26, 700003),
    (SPECIAL, "Kagerou Days: Natsu no Omoide", "special", 2019, "Лето 2019", 1, 700004),
    (MOVIE, "Hoshi no Koe", "movie", 2014, "Весна 2014", 1, 700005),
    (OTHER_MOVIE, "Hoshi no Kioku", "movie", 2003, "Осень 2003", 1, 700006),
]


def _seed_catalog() -> None:
    Base.metadata.create_all(engine)
    with session_scope() as session:
        session.execute(
            delete(EpisodeMapping).where(
                EpisodeMapping.series_id.in_([row[0] for row in CATALOG])
            )
        )
        for sid, title, type_, year, season, episodes, anilist in CATALOG:
            row = session.get(Series, sid)
            if row is None:
                row = Series(id=sid)
                session.add(row)
            row.title = title
            row.titles = {"romaji": title}
            row.all_titles = [title]
            row.type = type_
            row.year = year
            row.season = season
            row.number_of_episodes = episodes
            row.anilist_id = anilist


TVSHOW_NFO = """<?xml version="1.0" encoding="utf-8"?>
<tvshow><title>Kagerou Days</title><year>2019</year><anilistid>700001</anilistid></tvshow>
"""

FOREIGN_NFO = """<?xml version="1.0" encoding="utf-8"?>
<tvshow><title>Fuyu no Sonata</title><year>2009</year><anilistid>700001</anilistid></tvshow>
"""

MOVIE_NFO = """<?xml version="1.0" encoding="utf-8"?>
<movie><title>Hoshi no Koe</title><year>2014</year><anilistid>700005</anilistid></movie>
"""


def _season_nfo(number: int, premiered: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f"<season><seasonnumber>{number}</seasonnumber>"
        f"<premiered>{premiered}</premiered></season>\n"
    )


def _episode_nfo(season: int, episode: int) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f"<episodedetails><season>{season}</season><episode>{episode}</episode>"
        "<showtitle>Kagerou Days</showtitle></episodedetails>\n"
    )


def _run(root: Path, scope: str) -> int:
    """Полный конвейер по одному корню. Возвращает его id."""
    _seed_catalog()
    root_id = _persist_root(scan_root(str(root), scope))
    _map_folders(root_id, build_index(), scope)
    _map_episodes(root_id)
    return root_id


def _mapping(root_id: int) -> dict[str, int]:
    """Путь папки → тайтл, к которому она привязана."""
    with session_scope() as session:
        rows = session.execute(
            select(LibraryFolder.rel_path, TitleMapping.series_id)
            .join(TitleMapping, TitleMapping.folder_id == LibraryFolder.id)
            .where(LibraryFolder.root_id == root_id)
        ).all()
    return {rel: sid for rel, sid in rows}


def _episodes(series_id: int) -> set[float]:
    with session_scope() as session:
        return {
            episode
            for (episode,) in session.execute(
                select(EpisodeMapping.episode).where(
                    EpisodeMapping.series_id == series_id,
                    EpisodeMapping.role == "video",
                )
            ).all()
        }


def _write(folder: Path, name: str, text: str = "") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if text:
        (folder / name).write_text(text, encoding="utf-8")
    else:
        (folder / name).write_bytes(b"x" * 10)


# --------------------------------------------------------------------------- #
# Корень сериалов
# --------------------------------------------------------------------------- #


class TestSeriesRoot:
    def test_title_folder_with_files_inside(self, tmp_path: Path) -> None:
        """Клетка: сериалы · папка тайтла · свой .nfo · SxxExx · много видео."""
        root = tmp_path / "s1"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)
        for i in (1, 2, 3):
            _write(folder, f"Kagerou Days S01E0{i} [Group] WEBDL-1080p.mkv")

        root_id = _run(root, "series")

        assert _mapping(root_id)["Kagerou Days"] == SHOW
        assert _episodes(SHOW) == {1.0, 2.0, 3.0}

    def test_season_subfolder_takes_its_own_title(self, tmp_path: Path) -> None:
        """Клетка: сериалы · сезон 2 · родительский .nfo + season.nfo · SxxExx."""
        root = tmp_path / "s2"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        season = title / "Season 2"
        _write(season, "season.nfo", _season_nfo(2, "2021-01-10"))
        for i in (1, 2):
            _write(season, f"Kagerou Days S02E0{i} [Group] WEBDL-1080p.mkv")

        root_id = _run(root, "series")
        mapping = _mapping(root_id)

        assert mapping["Kagerou Days/Season 2"] == SHOW_S2, "уехало на спиноф или первый сезон"
        assert _episodes(SHOW_S2) == {1.0, 2.0}

    def test_specials_folder_goes_to_its_own_entry(self, tmp_path: Path) -> None:
        """Клетка: сериалы · спешлы · имя файла называет спешл · одно видео."""
        root = tmp_path / "s3"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        specials = title / "Specials"
        _write(specials, "Kagerou Days S00E01 - Natsu no Omoide [Group] Bluray-1080p.mkv")

        root_id = _run(root, "series")
        mapping = _mapping(root_id)

        assert mapping.get("Kagerou Days/Specials") == SPECIAL
        assert _episodes(SPECIAL) == {1.0}, "файл спешла не связался с серией"

    def test_episode_nfo_beats_the_file_name(self, tmp_path: Path) -> None:
        """Клетка: сериалы · папка тайтла · эпизодный .nfo · имя без номера."""
        root = tmp_path / "s4"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)
        _write(folder, "Kagerou Days - unparsable.mkv")
        _write(folder, "Kagerou Days - unparsable.nfo", _episode_nfo(1, 7))

        root_id = _run(root, "series")

        assert _episodes(SHOW) == {7.0}

    def test_season_from_file_names_without_subfolders(self, tmp_path: Path) -> None:
        """Клетка: сериалы · папка тайтла · номер сезона только в именах файлов."""
        root = tmp_path / "s5"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)
        for i in (1, 2):
            _write(folder, f"Kagerou Days S02E0{i} [Group] WEBDL-1080p.mkv")

        root_id = _run(root, "series")

        assert _mapping(root_id)["Kagerou Days"] == SHOW_S2
        assert _episodes(SHOW_S2) == {1.0, 2.0}

    def test_folder_without_any_nfo(self, tmp_path: Path) -> None:
        """Клетка: сериалы · папка тайтла · .nfo нет вовсе · только имя папки."""
        root = tmp_path / "s6"
        folder = root / "Kagerou Days"
        for i in (1, 2):
            _write(folder, f"Kagerou Days S01E0{i} [Group] WEBDL-1080p.mkv")

        root_id = _run(root, "series")

        assert _mapping(root_id)["Kagerou Days"] == SHOW

    def test_empty_title_folder_still_maps(self, tmp_path: Path) -> None:
        """Клетка: сериалы · папка тайтла · ноль видео.

        Требование отдельное: папка с одним `tvshow.nfo` должна мапиться и
        показывать ноль серий, а не пропадать.
        """
        root = tmp_path / "s7"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)

        root_id = _run(root, "series")

        assert _mapping(root_id).get("Kagerou Days") == SHOW

    def test_container_yields_to_the_subfolder(self, tmp_path: Path) -> None:
        """Клетка: сериалы · папка-контейнер · один случайный файл в корне."""
        root = tmp_path / "s8"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        # ⚠️ Файл намеренно **не** спешл. С `S00E01` вся папка тайтла
        # получала нулевой сезон и уезжала в спешлы — поведение верное, но
        # тест не про него.
        _write(title, "Kagerou Days S01E09 [Group] Bluray-1080p.mkv")
        season = title / "Season 1"
        _write(season, "season.nfo", _season_nfo(1, "2019-01-10"))
        for i in (1, 2, 3):
            _write(season, f"Kagerou Days S01E0{i} [Group] WEBDL-1080p.mkv")

        root_id = _run(root, "series")
        mapping = _mapping(root_id)

        assert mapping.get("Kagerou Days/Season 1") == SHOW
        # Не «не тот тайтл», а «привязки нет»: иначе тест прошёл бы и при
        # уходе контейнера на постороннюю запись.
        assert "Kagerou Days" not in mapping, "тайтл привязан дважды"

    def test_service_subfolder_does_not_inherit_the_parent(self, tmp_path: Path) -> None:
        """Клетка: сериалы · служебная подпапка · ноль видео."""
        root = tmp_path / "s9"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        for i in (1, 2):
            _write(title, f"Kagerou Days S01E0{i} [Group] WEBDL-1080p.mkv")
        subs = title / "subtitles"
        _write(subs, "Kagerou Days S01E01.ru.ass", "Диалог: раз два три")

        root_id = _run(root, "series")
        mapping = _mapping(root_id)

        # ⚠️ Утверждение положительное. Прежнее «не тайтл родителя» проходило
        # бы и тогда, когда служебная папка уехала на любой другой неверный
        # тайтл: отрицание не проверяет ничего, кроме одного значения.
        assert "Kagerou Days/subtitles" not in mapping, "служебная папка не тайтл"
        assert mapping["Kagerou Days"] == SHOW, "а сам тайтл при этом на месте"


# --------------------------------------------------------------------------- #
# Корень фильмов
# --------------------------------------------------------------------------- #


class TestMovieRoot:
    def test_single_movie_becomes_episode_one(self, tmp_path: Path) -> None:
        """Клетка: фильмы · папка фильма · movie.nfo · номера серии нет."""
        root = tmp_path / "m1"
        folder = root / "Hoshi no Koe"
        _write(folder, "movie.nfo", MOVIE_NFO)
        _write(folder, "Hoshi no Koe (2014) - [Bluray-1080p] [JA].mkv")
        _write(folder, "Hoshi no Koe (2014) - [Bluray-1080p] [JA].ja.srt", "текст")

        root_id = _run(root, "movie")

        assert _mapping(root_id)["Hoshi no Koe"] == MOVIE
        assert _episodes(MOVIE) == {1.0}

    def test_foreign_tvshow_nfo_is_ignored(self, tmp_path: Path) -> None:
        """Клетка: фильмы · рядом лежит мусорный tvshow.nfo о другом тайтле."""
        root = tmp_path / "m2"
        folder = root / "Hoshi no Koe"
        _write(folder, "movie.nfo", MOVIE_NFO)
        _write(folder, "tvshow.nfo", FOREIGN_NFO)
        _write(folder, "Hoshi no Koe (2014) - [Bluray-1080p] [JA].mkv")

        root_id = _run(root, "movie")

        assert _mapping(root_id)["Hoshi no Koe"] == MOVIE

    def test_similar_name_from_another_year_is_refused(self, tmp_path: Path) -> None:
        """Клетка: фильмы · похожее название · год расходится."""
        root = tmp_path / "m3"
        folder = root / "Hoshi no Koe"
        _write(folder, "movie.nfo", MOVIE_NFO)
        _write(folder, "Hoshi no Koe (2014) - [Bluray-1080p] [JA].mkv")

        root_id = _run(root, "movie")

        # Проверяем не «не уехал к соседу», а «попал куда надо»: отрицание
        # прошло бы и при полном отсутствии привязки.
        assert _mapping(root_id)["Hoshi no Koe"] == MOVIE
        assert _episodes(MOVIE) == {1.0}

    def test_series_does_not_land_in_the_movies_root(self, tmp_path: Path) -> None:
        """Клетка: фильмы · имя совпадает с сериалом · тип разводит."""
        root = tmp_path / "m4"
        folder = root / "Kagerou Days"
        _write(folder, "Kagerou Days (2019) - [Bluray-1080p] [JA].mkv")

        root_id = _run(root, "movie")

        mapping = _mapping(root_id)
        # Сериалу в корне фильмов место только у фильма — либо нигде.
        assert mapping.get("Kagerou Days") != SHOW
        assert mapping.get("Kagerou Days") in (None, MOVIE, OTHER_MOVIE)


# --------------------------------------------------------------------------- #
# Клетки, найденные при повторном выводе матрицы
# --------------------------------------------------------------------------- #


class TestFolderShapes:
    def test_release_subfolder_inside_a_title(self, tmp_path: Path) -> None:
        """Клетка: сериалы · релизная подпапка внутри тайтла.

        Реальная раскладка: `Yuru Camp/[Ohys-Raws] Yuru Camp Season 2 (BD …)`.
        Имя подпапки — это релизный тег плюс название с номером сезона.
        """
        root = tmp_path / "f1"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        release = title / "[Ohys-Raws] Kagerou Days 2nd Season (BD 1280x720 x264 AAC)"
        for i in (1, 2):
            _write(release, f"Kagerou Days 2nd Season - 0{i} (BD 1280x720 x264 AAC).mkv")

        root_id = _run(root, "series")
        mapping = _mapping(root_id)

        assert mapping.get(f"Kagerou Days/{release.name}") == SHOW_S2
        assert _episodes(SHOW_S2) == {1.0, 2.0}, "серии подпапки не связались"

    def test_specials_folder_named_extras(self, tmp_path: Path) -> None:
        """Клетка: сериалы · спешлы под именем `Extras`, а не `Specials`."""
        root = tmp_path / "f2"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        extras = title / "Extras"
        _write(extras, "Kagerou Days S00E01 - Natsu no Omoide [Group] Bluray-1080p.mkv")

        root_id = _run(root, "series")

        assert _mapping(root_id).get("Kagerou Days/Extras") == SPECIAL

    def test_excluded_files_do_not_count(self, tmp_path: Path) -> None:
        """Клетка: сериалы · бонусы NCOP/NCED рядом с сериями."""
        root = tmp_path / "f3"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)
        _write(folder, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")
        _write(folder, "Kagerou Days - NCOP (BD 1920x1080).mkv")
        _write(folder, "Kagerou Days - NCED (BD 1920x1080).mkv")

        root_id = _run(root, "series")

        assert _episodes(SHOW) == {1.0}, "бонусы засчитались как серии"

    def test_folder_with_subtitles_only(self, tmp_path: Path) -> None:
        """Клетка: сериалы · подпапка без единого видео, только субтитры.

        Реальная: `Yuru Camp/[Ohys-Raws] …` — там тринадцать `.ja.srt` и ни
        одного видео (единственный `.mkv` там — спешл, он исключается).
        """
        root = tmp_path / "f4"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        _write(title, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")
        subs = title / "[Ohys-Raws] Kagerou Days (BD)"
        for i in (1, 2):
            _write(subs, f"Kagerou Days - 0{i} (BD 1280x720).ja.srt", "こんにちは。元気ですか。" * 20)

        root_id = _run(root, "series")

        # ⚠️ Прежнее утверждение проверяло серии **родителя** и к самой
        # подпапке отношения не имело.
        #
        # Проверено поведение: подпапка привязывается к тому же тайтлу, и это
        # правильно — субтитры принадлежат ему. Я ожидал обратного («видео нет
        # — привязки нет») и ошибся: пустая по видео папка всё равно часть
        # тайтла, иначе её субтитры некуда было бы отнести.
        mapping = _mapping(root_id)
        assert mapping.get("Kagerou Days/[Ohys-Raws] Kagerou Days (BD)") == SHOW
        # Серии при этом появляются только от настоящих видеофайлов.
        assert _episodes(SHOW) == {1.0}


class TestSubtitleRolesThroughThePipeline:
    def test_sidecar_language_reaches_the_role(self, tmp_path: Path) -> None:
        """Клетка: субтитры · язык из суффикса · роль в связях серий."""
        root = tmp_path / "f5"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)
        _write(folder, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")
        _write(folder, "Kagerou Days S01E01 [Group] WEBDL-1080p.ru.ass", "Привет, как дела? " * 40)

        root_id = _run(root, "series")

        with session_scope() as session:
            roles = {
                role
                for (role,) in session.execute(
                    select(EpisodeMapping.role).where(EpisodeMapping.series_id == SHOW)
                ).all()
            }
        assert roles == {"video", "sub_ru"}

    def test_language_from_script_when_suffix_is_missing(self, tmp_path: Path) -> None:
        """Клетка: субтитры без суффикса · язык по письменности."""
        root = tmp_path / "f6"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)
        _write(folder, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")
        _write(folder, "Kagerou Days S01E01 [Group] WEBDL-1080p.ass", "Привет, как дела? " * 40)

        root_id = _run(root, "series")

        with session_scope() as session:
            roles = {
                role
                for (role,) in session.execute(
                    select(EpisodeMapping.role).where(EpisodeMapping.series_id == SHOW)
                ).all()
            }
        # Полный набор, а не вхождение: при проверке «`sub_ru` есть среди»
        # тест прошёл бы и с лишней ошибочной ролью рядом.
        assert roles == {"video", "sub_ru"}


class TestMovieShapes:
    def test_several_parts_in_one_folder(self, tmp_path: Path) -> None:
        """Клетка: фильмы · несколько видео в одной папке.

        Реальная: `Gekijou Soushuuhen Bocchi the Rock! Movie + Tokuten BD`.
        Правило «единственное видео — первая серия» тут намеренно молчит, и
        сейчас у файлов не остаётся номера вовсе. Тест фиксирует **текущее**
        поведение, чтобы изменение было заметным.
        """
        root = tmp_path / "f7"
        folder = root / "Hoshi no Koe"
        _write(folder, "movie.nfo", MOVIE_NFO)
        _write(folder, "Hoshi no Koe (2014) - Part 1 [Bluray-1080p].mkv")
        _write(folder, "Hoshi no Koe (2014) - Part 2 [Bluray-1080p].mkv")

        root_id = _run(root, "movie")

        assert _mapping(root_id)["Hoshi no Koe"] == MOVIE
        assert _episodes(MOVIE) == set(), "номеров нет — это известная граница"

    def test_season_nfo_inside_a_movie_folder(self, tmp_path: Path) -> None:
        """Клетка: фильмы · рядом лежит season.nfo.

        Реальная: у обеих папок Girls Band Cry в корне фильмов есть `season.nfo`.
        Он не должен перебивать `movie.nfo`.
        """
        root = tmp_path / "f8"
        folder = root / "Hoshi no Koe"
        _write(folder, "movie.nfo", MOVIE_NFO)
        _write(folder, "season.nfo", _season_nfo(1, "2014-05-24"))
        _write(folder, "Hoshi no Koe (2014) - [Bluray-1080p] [JA].mkv")

        root_id = _run(root, "movie")

        assert _mapping(root_id)["Hoshi no Koe"] == MOVIE
        assert _episodes(MOVIE) == {1.0}


class TestAmbiguityAndCollisions:
    def test_specials_with_several_candidates_ask_for_confirmation(self, tmp_path: Path) -> None:
        """Клетка: спешлы · несколько подходящих вхождений · имя не разводит.

        Реальная: у K-On! четыре спешла. Когда имя файла не называет ни одного
        из них, выбрать нечего — и решать за человека нельзя.
        """
        _seed_catalog()
        with session_scope() as session:
            second = session.get(Series, 900107)
            if second is None:
                second = Series(id=900107)
                session.add(second)
            second.title = "Kagerou Days: Fuyu no Omoide"
            second.titles = {"romaji": "Kagerou Days: Fuyu no Omoide"}
            second.all_titles = ["Kagerou Days: Fuyu no Omoide"]
            second.type = "special"
            second.year = 2019
            second.number_of_episodes = 1

        root = tmp_path / "c1"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        specials = title / "Specials"
        _write(specials, "Kagerou Days S00E01 [Group] Bluray-1080p.mkv")

        root_id = _persist_root(scan_root(str(root), "series"))
        _map_folders(root_id, build_index(), "series")

        # ⚠️ Отбор обязательно по корню. Папка `Kagerou Days/Specials` есть и в
        # других клетках матрицы — база у тестов общая, и запрос по одному лишь
        # пути подхватывал чужую привязку.
        with session_scope() as session:
            rows = session.execute(
                select(TitleMapping.series_id, TitleMapping.needs_confirmation)
                .join(LibraryFolder, TitleMapping.folder_id == LibraryFolder.id)
                .where(
                    LibraryFolder.root_id == root_id,
                    LibraryFolder.rel_path == "Kagerou Days/Specials",
                )
            ).all()

        assert rows, "папка спешлов вообще не привязалась"
        assert all(flag for _sid, flag in rows), "выбор наугад между спешлами"
        # И привязка ушла именно к спешлу, а не к сериалу.
        assert all(sid in {SPECIAL, 900107} for sid, _flag in rows)

    def test_own_translation_id_beats_the_folder(self, tmp_path: Path) -> None:
        """Клетка: файл с `t{id}` чужого тайтла лежит в папке сериала.

        Реальная: спешл `[Anime365] Toradora Bentou no Gokui - 01 (t924861)`
        лежит в корне папки `Toradora!`. Он принадлежит своему вхождению.
        """
        _seed_catalog()
        with session_scope() as session:
            session.execute(delete(Translation).where(Translation.id == 950901))
            session.execute(delete(Episode).where(Episode.id == 950902))
            session.add(Episode(id=950902, series_id=SPECIAL, episode_num=1))
            session.add(Translation(id=950901, series_id=SPECIAL, episode_id=950902))

        root = tmp_path / "c2"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)
        _write(folder, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")
        _write(folder, "[Anime365] Kagerou Days Natsu no Omoide - 01 (t950901).mkv")

        root_id = _persist_root(scan_root(str(root), "series"))
        _map_folders(root_id, build_index(), "series")
        _map_episodes(root_id)

        assert _episodes(SHOW) == {1.0}
        assert _episodes(SPECIAL) == {1.0}, "файл со своим t{id} остался за папкой"

    def test_combined_episode_file(self, tmp_path: Path) -> None:
        """Клетка: одна серия-склейка `S01E01-E11` вместо одиннадцати файлов.

        Реальная: Wotakoi. Сейчас засчитывается как первая серия — диапазон
        разбирается, но в связи попадает только начало.
        """
        root = tmp_path / "c3"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)
        _write(folder, "Kagerou Days S01E01-E11 - Everything [Sonarr] Bluray-1080p.mkv")

        root_id = _run(root, "series")

        assert _episodes(SHOW) == {1.0}
        # Конец диапазона разобран — просто пока не превращается в связи.
        with session_scope() as session:
            ends = {
                end
                for (end,) in session.execute(
                    select(LibraryFile.episode_end)
                    .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
                    .where(LibraryFolder.root_id == root_id)
                ).all()
            }
        assert 11.0 in ends, "диапазон S01E01-E11 не разобран"

    def test_same_title_in_two_folders_picks_a_primary(self, tmp_path: Path) -> None:
        """Клетка: один тайтл лежит в двух папках · выбор основной.

        Реальная: `Toradora!` и `Toradora 10th Anniversary 10bit` — один и тот
        же сезон в разных кодеках. Обе привязки законны, но качать надо в одну.
        """
        root = tmp_path / "c4"
        first = root / "Kagerou Days"
        _write(first, "tvshow.nfo", TVSHOW_NFO)
        for i in (1, 2, 3):
            _write(first, f"Kagerou Days S01E0{i} [Group] WEBDL-1080p.mkv")
        second = root / "Kagerou Days 10bit"
        _write(second, "tvshow.nfo", TVSHOW_NFO)
        _write(second, "Kagerou Days S01E01 [Other] Bluray-1080p.mkv")

        root_id = _run(root, "series")

        def primaries() -> list[tuple[str, bool]]:
            with session_scope() as session:
                return list(
                    session.execute(
                        select(LibraryFolder.rel_path, TitleMapping.is_primary)
                        .join(TitleMapping, TitleMapping.folder_id == LibraryFolder.id)
                        .where(
                            LibraryFolder.root_id == root_id,
                            TitleMapping.series_id == SHOW,
                        )
                    ).all()
                )

        assert len(primaries()) == 2, "обе папки должны остаться привязанными"

        # ⚠️ Сразу после скана основной нет, и это устройство, а не упущение:
        # выбор делается лениво — при открытии страницы тайтла и перед
        # загрузкой. Тест проверяет **контракт**, а не момент времени.
        assert sum(1 for _rel, flag in primaries() if flag) == 0

        ensure_primary_mapping(SHOW)

        rows = primaries()
        assert sum(1 for _rel, flag in rows if flag) == 1, "основная должна быть одна"
        # Побеждает та папка, где файлов больше: туда и поедут загрузки.
        primary = next(rel for rel, flag in rows if flag)
        assert primary == "Kagerou Days"

    def test_several_seasons_flat_in_one_folder(self, tmp_path: Path) -> None:
        """Клетка: сериалы · несколько сезонов вповалку в одной папке.

        Подпапок нет, файлы идут `S01E01`, `S02E01`. Единогласия по сезону нет,
        поэтому папка остаётся за первым сезоном — а файлы второго садятся на
        те же номера серий. Тест фиксирует **текущее** поведение: это известная
        граница, а не работающий случай.
        """
        root = tmp_path / "c5"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)
        _write(folder, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")
        _write(folder, "Kagerou Days S02E01 [Group] WEBDL-1080p.mkv")

        root_id = _run(root, "series")

        assert _mapping(root_id)["Kagerou Days"] == SHOW
        # Обе первые серии засчитаны одному тайтлу — вот она, граница.
        assert _episodes(SHOW) == {1.0}
        # Файлы при этом оба на месте: теряется не файл, а различие сезонов.
        with session_scope() as session:
            count = len(
                session.execute(
                    select(LibraryFile.id)
                    .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
                    .where(LibraryFolder.root_id == root_id, LibraryFile.kind == "video")
                ).all()
            )
        assert count == 2


class TestRemainingCombinations:
    """Клетки, которых в живой библиотеке не встречалось, но они выводятся."""

    def test_third_season(self, tmp_path: Path) -> None:
        """Клетка: сезон 3 — проверялся только второй."""
        _seed_catalog()
        with session_scope() as session:
            third = session.get(Series, 900108)
            if third is None:
                third = Series(id=900108)
                session.add(third)
            third.title = "Kagerou Days 3rd Season"
            third.titles = {"romaji": "Kagerou Days 3rd Season"}
            third.all_titles = ["Kagerou Days 3rd Season"]
            third.type = "tv"
            third.year = 2023
            third.season = "Зима 2023"
            third.number_of_episodes = 13

        root = tmp_path / "r1"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        season = title / "Season 3"
        _write(season, "season.nfo", _season_nfo(3, "2023-01-10"))
        _write(season, "Kagerou Days S03E01 [Group] WEBDL-1080p.mkv")

        root_id = _persist_root(scan_root(str(root), "series"))
        _map_folders(root_id, build_index(), "series")

        assert _mapping(root_id)["Kagerou Days/Season 3"] == 900108

    def test_specials_next_to_several_seasons(self, tmp_path: Path) -> None:
        """Клетка: `Specials` соседствует с двумя сезонами."""
        root = tmp_path / "r2"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        first = title / "Season 1"
        _write(first, "season.nfo", _season_nfo(1, "2019-01-10"))
        _write(first, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")
        second = title / "Season 2"
        _write(second, "season.nfo", _season_nfo(2, "2021-01-10"))
        _write(second, "Kagerou Days S02E01 [Group] WEBDL-1080p.mkv")
        specials = title / "Specials"
        _write(specials, "Kagerou Days S00E01 - Natsu no Omoide [Group] Bluray-1080p.mkv")

        root_id = _run(root, "series")
        mapping = _mapping(root_id)

        assert mapping["Kagerou Days/Season 1"] == SHOW
        assert mapping["Kagerou Days/Season 2"] == SHOW_S2
        assert mapping["Kagerou Days/Specials"] == SPECIAL

    def test_more_local_episodes_than_the_catalog_knows(self, tmp_path: Path) -> None:
        """Клетка: локальных серий больше, чем `numberOfEpisodes`.

        Требование из ТЗ: показываем локальное число, а не обрезаем по каталогу.
        """
        root = tmp_path / "r3"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)
        for i in range(1, 16):  # каталог знает про 13
            _write(folder, f"Kagerou Days S01E{i:02d} [Group] WEBDL-1080p.mkv")

        root_id = _run(root, "series")

        assert _mapping(root_id)["Kagerou Days"] == SHOW
        assert len(_episodes(SHOW)) == 15, "лишние серии потерялись"

    def test_two_videos_for_one_episode(self, tmp_path: Path) -> None:
        """Клетка: на одну серию два видео — озвучка и оригинал."""
        root = tmp_path / "r4"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)
        _write(folder, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")
        _write(folder, "Kagerou Days S01E01 [Other] Bluray-1080p.ru.mkv")

        root_id = _run(root, "series")

        assert _episodes(SHOW) == {1.0}
        with session_scope() as session:
            count = len(
                session.execute(
                    select(EpisodeMapping.id).where(
                        EpisodeMapping.series_id == SHOW, EpisodeMapping.role == "video"
                    )
                ).all()
            )
        assert count == 2, "второй файл потерялся, хотя это другая версия той же серии"

    def test_title_without_anilist_id(self, tmp_path: Path) -> None:
        """Клетка: у тайтла нет `anilist_id` — таких в каталоге десять тысяч.

        Работать должен один строковый путь, без единого идентификатора.
        """
        _seed_catalog()
        with session_scope() as session:
            session.get(Series, SHOW).anilist_id = None

        root = tmp_path / "r5"
        folder = root / "Kagerou Days"
        _write(folder, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")

        root_id = _persist_root(scan_root(str(root), "series"))
        _map_folders(root_id, build_index(), "series")

        assert _mapping(root_id)["Kagerou Days"] == SHOW

    def test_manual_mapping_survives_a_rescan(self, tmp_path: Path) -> None:
        """Клетка: ручная привязка не перетирается сканом."""
        root = tmp_path / "r6"
        folder = root / "Kagerou Days"
        _write(folder, "tvshow.nfo", TVSHOW_NFO)
        _write(folder, "Kagerou Days S01E01 [Group] WEBDL-1080p.mkv")

        root_id = _run(root, "series")
        with session_scope() as session:
            folder_id = session.execute(
                select(LibraryFolder.id).where(
                    LibraryFolder.root_id == root_id, LibraryFolder.rel_path == "Kagerou Days"
                )
            ).scalar_one()
            session.execute(delete(TitleMapping).where(TitleMapping.folder_id == folder_id))
            session.add(
                TitleMapping(
                    series_id=SHOW_S2,
                    folder_id=folder_id,
                    source="manual",
                    confidence=100.0,
                    is_manual=True,
                )
            )

        _persist_root(scan_root(str(root), "series"))
        _map_folders(root_id, build_index(), "series")

        assert _mapping(root_id)["Kagerou Days"] == SHOW_S2, "скан перетёр решение человека"

    def test_where_the_nesting_limit_actually_is(self, tmp_path: Path) -> None:
        """Клетка: докуда доходит обход вложенных папок.

        Я предполагал, что предел — два уровня, и ошибся: релизная подпапка
        внутри сезона (третий уровень) обходится. Не обходится четвёртый.
        Тест фиксирует настоящую границу, потому что она наблюдаемая:
        файлы глубже просто не попадают в базу.
        """
        root = tmp_path / "r7"
        third = root / "Kagerou Days" / "Season 1" / "[Group] Extras"
        _write(third, "Kagerou Days S01E09 [Group] WEBDL-1080p.mkv")
        fourth = third / "Bonus"
        _write(fourth, "Kagerou Days S01E10 [Group] WEBDL-1080p.mkv")

        root_id = _persist_root(scan_root(str(root), "series"))
        with session_scope() as session:
            paths = {
                rel
                for (rel,) in session.execute(
                    select(LibraryFolder.rel_path).where(LibraryFolder.root_id == root_id)
                ).all()
            }

        assert "Kagerou Days/Season 1/[Group] Extras" in paths, "третий уровень обходится"
        assert "Kagerou Days/Season 1/[Group] Extras/Bonus" not in paths, "четвёртый — нет"

    def test_unreadable_subtree_keeps_its_data(self, tmp_path: Path) -> None:
        """Клетка: поддерево не удалось прочитать — данные остаются.

        Пустой список файлов там означает «неизвестно», а не «пусто». Без этой
        оговорки отвалившееся на секунду монтирование стёрло бы половину базы
        вместе с ручными привязками.
        """
        root = tmp_path / "r8"
        title = root / "Kagerou Days"
        _write(title, "tvshow.nfo", TVSHOW_NFO)
        season = title / "Season 1"
        _write(season, "season.nfo", _season_nfo(1, "2019-01-10"))
        for i in (1, 2):
            _write(season, f"Kagerou Days S01E0{i} [Group] WEBDL-1080p.mkv")

        root_id = _run(root, "series")
        assert "Kagerou Days/Season 1" in _mapping(root_id)

        # Так выглядит проход, где папку не удалось открыть.
        result = scan_root(str(root), "series")
        result.folders[0].children = []
        result.folders[0].read_failed = True
        _persist_root(result)

        with session_scope() as session:
            paths = {
                rel
                for (rel,) in session.execute(
                    select(LibraryFolder.rel_path).where(LibraryFolder.root_id == root_id)
                ).all()
            }
        assert "Kagerou Days/Season 1" in paths, "нечитаемое поддерево стёрли"
        assert _episodes(SHOW) == {1.0, 2.0}, "связи серий потеряны"

    def test_same_title_in_both_roots(self, tmp_path: Path) -> None:
        """Клетка: франшиза лежит и в сериалах, и в фильмах одновременно.

        Реальная раскладка Girls Band Cry: сериал в одном корне, фильмы в
        другом. Корни не должны мешать друг другу — тип разводит их сам.
        """
        series_root = tmp_path / "r9-series"
        _write(series_root / "Kagerou Days", "tvshow.nfo", TVSHOW_NFO)
        for i in (1, 2):
            _write(series_root / "Kagerou Days", f"Kagerou Days S01E0{i} [G] WEBDL-1080p.mkv")

        movie_root = tmp_path / "r9-movies"
        _write(movie_root / "Hoshi no Koe", "movie.nfo", MOVIE_NFO)
        _write(movie_root / "Hoshi no Koe", "Hoshi no Koe (2014) - [Bluray-1080p].mkv")

        series_id_root = _run(series_root, "series")
        movie_id_root = _persist_root(scan_root(str(movie_root), "movie"))
        _map_folders(movie_id_root, build_index(), "movie")
        _map_episodes(movie_id_root)

        assert _mapping(series_id_root)["Kagerou Days"] == SHOW
        assert _mapping(movie_id_root)["Hoshi no Koe"] == MOVIE
        assert _episodes(SHOW) == {1.0, 2.0}
        assert _episodes(MOVIE) == {1.0}
