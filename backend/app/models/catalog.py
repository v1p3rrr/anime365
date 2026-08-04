"""Модели каталога Anime365.

Кэш локальный: UI работает только с ним, в сеть за каталогом не ходит.
Объём — 29 231 тайтл, порядка 25 МБ, поэтому храним всё целиком.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Series(Base):
    """Тайтл каталога.

    ⚠️ **Таблица широкая.** В каждой строке лежат `titles`, `all_titles`,
    `descriptions` и `links` — вместе это около 25 МБ на 29 тысяч строк. Значит
    любой полный скан читает всю эту массу с диска, даже если из строки нужен
    один `id`. Замерено в контейнере: `SELECT COUNT(*) WHERE is_hentai = 0`
    занимал **1,2 секунды**, а `COUNT(*)` без условий — 0,2 мс.

    Отсюда составные индексы ниже: они **покрывающие**, то есть запрос
    отвечается по самому индексу и до строк таблицы дело не доходит. Тот же
    COUNT после этого — 0,6 мс, каталог по умолчанию (сортировка по оценке) —
    0,2 мс вместо 1,2 с.
    """

    __tablename__ = "series"

    __table_args__ = (
        # Первое поле везде `is_hentai`: оно есть в WHERE почти каждого запроса
        # каталога (по умолчанию скрываем), а ведущая колонка индекса обязана
        # совпадать с условием, иначе индекс не применится.
        #
        # ⚠️ Набор подбирался ЗАМЕРАМИ ВСЕЙ батареи запросов, а не по одному.
        # Неполный набор уводит планировщик: с двумя индексами вместо шести
        # сортировка по году стала 3,3 секунды вместо 0,3 мс — SQLite выбрал
        # новый индекс и потерял старый `ix_series_year`. Добавляя сюда что-то,
        # прогоняйте `EXPLAIN QUERY PLAN` по всем сортировкам и фильтрам.
        #
        # ⚠️ `ANALYZE` не запускаем: с собранной статистикой запрос «жанр плюс
        # сортировка по оценке» деградировал с 2 мс до 377 мс. Проверено —
        # без статистики планировщик выбирает покрывающий индекс, со статистикой
        # уходит в скан.
        #
        # Устройство: ведущая колонка — `is_hentai` (условие есть почти в каждом
        # запросе), вторая — колонка сортировки, дальше `id` и **все остальные
        # фильтруемые колонки**. Последнее — не запас на будущее, а главное:
        # человек фильтрует по одному полю, а сортирует по другому, и если
        # фильтруемой колонки в индексе нет, SQLite дочитывает её из широкой
        # таблицы. Замерено: фильтр по сезону с сортировкой по оценке стоил
        # 3,7 с ровно поэтому ([D-84](../../../claude/09-decisions.md)).
        #
        # `title` лежит только в своём индексе: это самая тяжёлая колонка, а
        # фильтра по названию нет вовсе — поиск идёт через FTS и приходит
        # списком идентификаторов.
        # Индексов ровно столько, сколько порядков сортировки в интерфейсе.
        # Отдельные узкие индексы под частые фильтры (тип, онгоинги) отсюда
        # убраны намеренно: они не содержат колонку сортировки, и планировщик,
        # выбрав такой при равной оценке стоимости, снова уходит читать таблицу.
        # Замерено — с ними батарея стоит 211 мс, без них 145 мс.
        Index("ix_series_catalog_id", "is_hentai", "id", "mal_score", "year", "type", "season_name", "is_airing"),
        Index("ix_series_catalog_score", "is_hentai", "mal_score", "id", "year", "type", "season_name", "is_airing"),
        Index("ix_series_catalog_year", "is_hentai", "year", "id", "mal_score", "type", "season_name", "is_airing"),
        Index("ix_series_catalog_title", "is_hentai", "title", "id", "mal_score", "year", "type", "season_name", "is_airing"),
    )

    # id из Anime365, не автоинкремент.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    # titles — словарь {ru, romaji, ja, en, short}; all_titles — плоский список всех вариантов.
    # Матчинг локальных папок опирается на all_titles: titles.en заполнен лишь у 46% тайтлов,
    # тогда как all_titles есть у всех и содержит в среднем 7.6 вариантов названия.
    titles: Mapped[dict] = mapped_column(JSON, default=dict)
    all_titles: Mapped[list] = mapped_column(JSON, default=list)
    title: Mapped[str] = mapped_column(String(512), default="")

    type: Mapped[str] = mapped_column(String(32), default="", index=True)
    type_title: Mapped[str] = mapped_column(String(64), default="")
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    # Строка вида "Осень 2015" — именно по ней разводятся сезоны при маппинге
    # (дата <premiered> из season.nfo отображается на это поле).
    season: Mapped[str | None] = mapped_column(String(64), index=True)
    # Сезон отдельно от года, чтобы фильтровать «все зимние» независимо от года.
    # В API это одна строка, поэтому разбираем при синхронизации.
    season_name: Mapped[str | None] = mapped_column(String(16), index=True)

    # ВНИМАНИЕ: это запланированное число серий, а не выложенное.
    # У онгоингов реальных эпизодов в API бывает заметно меньше.
    number_of_episodes: Mapped[int] = mapped_column(Integer, default=0)

    poster_url: Mapped[str | None] = mapped_column(String(512))
    poster_url_small: Mapped[str | None] = mapped_column(String(512))
    url: Mapped[str | None] = mapped_column(String(512))

    mal_id: Mapped[int | None] = mapped_column(Integer, index=True)
    anilist_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # aniDbId намеренно не индексируем как ключ: он не уникален
    # (у всех трёх фильмов Heaven's Feel он одинаковый — 10755).
    anidb_id: Mapped[int | None] = mapped_column(Integer)
    # Единственная оценка, которую показываем. Оценки AniList в API нет (только
    # anilist_id), а worldArtScore пользователь решил не выводить.
    mal_score: Mapped[float | None] = mapped_column(Float)

    is_airing: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_hentai: Mapped[bool] = mapped_column(Boolean, default=False)

    # Подгружаются только в детальном запросе тайтла.
    descriptions: Mapped[list | None] = mapped_column(JSON)
    links: Mapped[list | None] = mapped_column(JSON)
    # Заполнены ли поля, которые приходят только из GET /api/series/{id}.
    has_details: Mapped[bool] = mapped_column(Boolean, default=False)

    # Решение пользователя «это фильм / это сериал». None — определять по типу.
    # Автоматически развести случаи нельзя: в каталоге есть тайтлы с type=movie
    # и несколькими эпизодами (связка из 2-3 фильмов), и Radarr-фильмом такое
    # не является. От этого зависит, в какой корень лягут загрузки.
    treat_as_movie: Mapped[bool | None] = mapped_column(Boolean)

    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    genres: Mapped[list[Genre]] = relationship(
        secondary="series_genre", back_populates="series", lazy="selectin"
    )
    episodes: Mapped[list[Episode]] = relationship(
        back_populates="series", cascade="all, delete-orphan"
    )


class Genre(Base):
    __tablename__ = "genre"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String(128), default="")

    series: Mapped[list[Series]] = relationship(secondary="series_genre", back_populates="genres")


class SeriesGenre(Base):
    __tablename__ = "series_genre"

    # Первичный ключ идёт (series_id, genre_id), поэтому отбор «все тайтлы
    # такого-то жанра» по нему не ищется, а просматривает таблицу целиком.
    # Обратный порядок даёт покрывающий поиск. Замерено на 29 214 тайтлах:
    # фильтр по жанру 2,3 → 1,1 мс, `COUNT` по нему 63 → 3,6 мс, а список
    # жанров с числами (он строится на каждое открытие фильтров) 29 → 3,5 мс.
    __table_args__ = (Index("ix_series_genre_genre", "genre_id", "series_id"),)

    series_id: Mapped[int] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), primary_key=True
    )
    genre_id: Mapped[int] = mapped_column(
        ForeignKey("genre.id", ondelete="CASCADE"), primary_key=True
    )


class Episode(Base):
    __tablename__ = "episode"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), index=True
    )

    # В API это строка ("6"), а не число. Храним и строку, и разобранное число:
    # по числу мапим на локальные файлы, строку показываем как есть.
    episode_int: Mapped[str] = mapped_column(String(16), default="")
    episode_num: Mapped[float | None] = mapped_column(Float, index=True)
    episode_full: Mapped[str] = mapped_column(String(128), default="")
    episode_title: Mapped[str] = mapped_column(String(512), default="")

    # tv | movie | ova | ona | special | preview | ...
    # ВАЖНО: preview (трейлеры) лежат вперемешку с сериями и имеют собственный episode_int,
    # поэтому фильтрация по типу обязательна.
    episode_type: Mapped[str] = mapped_column(String(32), default="", index=True)

    first_uploaded: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    series: Mapped[Series] = relationship(back_populates="episodes")


class Translation(Base):
    __tablename__ = "translation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), index=True
    )
    episode_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # type: voiceRu | subRu | voiceEn | subEn | subJa | raw | voiceOther
    type: Mapped[str] = mapped_column(String(32), default="", index=True)
    # type_kind: voice | sub | raw | voiceOth
    type_kind: Mapped[str] = mapped_column(String(16), default="", index=True)
    # type_lang: ru | en | ja | er(other — нас не интересует)
    type_lang: Mapped[str] = mapped_column(String(8), default="", index=True)

    authors_summary: Mapped[str] = mapped_column(String(512), default="")
    authors: Mapped[list] = mapped_column(JSON, default=list)
    quality_type: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(Text)

    # Разрешение исходника. Доступно в дешёвом списочном запросе, поэтому используется
    # как ОЦЕНКА максимального качества в сводке по тайтлу. Точные ступени скачивания
    # из него не выводятся (лестница нелинейна) — за ними нужен embed.
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[float | None] = mapped_column(Float)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int | None] = mapped_column(Integer)
    # Ссылка на страницу конкретного перевода конкретной серии на сайте —
    # именно она нужна кнопке «смотреть», а не общая страница тайтла.
    url: Mapped[str | None] = mapped_column(String(512))
    added_at: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[str | None] = mapped_column(String(32))
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TranslationEmbed(Base):
    """Ленивый кэш ответа embed.

    Каждая запись стоит одного запроса к API, а у тайтла бывает ~2000 переводов,
    поэтому заполняется только по мере надобности: при раскрытии группы в UI
    и перед скачиванием.
    """

    __tablename__ = "translation_embed"

    translation_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    # Ключевой признак: у перевода с type_kind == "sub" отсутствие subtitles_url
    # означает ВШИТЫЕ субтитры. Другого способа это узнать в API нет.
    has_subtitles: Mapped[bool] = mapped_column(Boolean, default=False)
    subtitles_url: Mapped[str | None] = mapped_column(String(512))

    # Реально доступные ступени скачивания, например [1080, 720, 360].
    download_heights: Mapped[list] = mapped_column(JSON, default=list)

    # Ступени для просмотра. Отдельным полем: в ответе embed это другой список,
    # и совпадение наборов ниоткуда не следует.
    stream_heights: Mapped[list] = mapped_column(JSON, default=list)

    # Вес файла по каждой ступени: {"1080": 229376000, "720": 83960238}.
    # Заполняется отдельно от ступеней: чтобы узнать размер, нужен HEAD-запрос
    # на CDN по каждой ступени, а ступени приходят одним ответом embed.
    # Ключи строковые — JSON других не знает.
    download_sizes: Mapped[dict] = mapped_column(JSON, default=dict)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ScanRun(Base):
    """История фоновых джоб — для индикаторов в UI и для диагностики."""

    __tablename__ = "scan_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # catalog_full | catalog_incr | catalog_airing | library | ffprobe
    kind: Mapped[str] = mapped_column(String(32), index=True)
    # running | ok | failed
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    stats: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class SeriesRelations(Base):
    """Порядок просмотра франшизы — из Шикимори.

    Ключ — `mal_id`, а не наш `series_id`: связи приходят в терминах Шикимори,
    и часть узлов относится к тайтлам, которых у нас в каталоге нет вовсе.
    Привязка к `series` сделала бы такие узлы негде хранить.

    Узлы лежат готовым списком: разбирать граф `links` каждый раз незачем —
    порядок просмотра уже выражен полем `weight` у самих узлов.
    """

    __tablename__ = "series_relations"

    mal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    nodes: Mapped[list] = mapped_column(JSON, default=list)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class SeriesDetails(Base):
    """Сведения о тайтле, которых нет в API Anime365, — из Шикимори.

    Ключ тот же, что у связей: `mal_id`. Anime365 не отдаёт ни студию, ни
    состояние выхода, а Шикимори отдаёт то и другое одним запросом
    `/api/animes/{mal_id}` — отдельно ходить незачем.

    `status` принимает значения `anons`, `ongoing`, `released` — это ответ
    Шикимори дословно, без перевода в наши термины: пусть в базе лежит то, что
    сказал источник, а трактовка живёт в коде.

    `studios` — список как пришёл: `{id, name, image}`. Картинка отдаётся
    относительным путём (`/system/studios/original/11.png?...`), поэтому при
    показе к ней приписывается адрес Шикимори.
    """

    __tablename__ = "series_details"

    mal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    studios: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str | None] = mapped_column(String(32))
    aired_on: Mapped[str | None] = mapped_column(String(16))
    released_on: Mapped[str | None] = mapped_column(String(16))
    episodes_aired: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[str | None] = mapped_column(String(32))
    duration: Mapped[int | None] = mapped_column(Integer)
    # Дата следующей серии — только у онгоингов, у остальных пусто.
    next_episode_at: Mapped[str | None] = mapped_column(String(32))
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class SeriesTranslations(Base):
    """Какие типы переводов есть у тайтла НА САЙТЕ.

    Не путать с покрытием библиотеки: там считается, что уже скачано, а здесь —
    что вообще доступно к скачиванию. Для фильтра «покажи тайтлы с японскими
    субтитрами» нужно именно второе.

    Отдельная таблица, а не поля в `series`, по двум причинам. Данные приходят
    другим путём (по запросу на тайтл, а не из списочной выдачи) и обновляются
    своим расписанием, поэтому полный рескан каталога не должен их затирать.
    И `checked_at` здесь про конкретно эту проверку: у `series` своей отметки
    свежести нет.

    Счётчики, а не флаги: «есть RU-озвучка» и «есть 27 RU-озвучек» — разные
    сообщения, а фильтру `> 0` считается так же дёшево, как `is true`.
    """

    __tablename__ = "series_translations"

    series_id: Mapped[int] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )

    sub_ja: Mapped[int] = mapped_column(Integer, default=0)
    sub_ru: Mapped[int] = mapped_column(Integer, default=0)
    sub_en: Mapped[int] = mapped_column(Integer, default=0)
    raw_ja: Mapped[int] = mapped_column(Integer, default=0)
    voice_ru: Mapped[int] = mapped_column(Integer, default=0)
    voice_en: Mapped[int] = mapped_column(Integer, default=0)

    # Все переводы тайтла, включая те, что не попали ни в один из шести типов
    # (украинская озвучка, voiceOth). Расхождение с суммой — не ошибка.
    total: Mapped[int] = mapped_column(Integer, default=0)

    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


Index("ix_translation_series_lang", Translation.series_id, Translation.type_lang)
Index("ix_episode_series_type", Episode.series_id, Episode.episode_type)
