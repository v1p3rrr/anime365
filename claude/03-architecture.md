# Архитектура

> Состояние на 2026-07-30. Ниже описано то, что **реально написано**; чего ещё нет — помечено.

> **Зачем этот файл.** Карта кода: какой модуль за что отвечает (описания взяты
> из докстрок самих модулей), стек и почему выбран, схема БД, устройство
> индексов каталога с замерами, реестр настроек, ключевые сервисы, черновик API.
> **Когда открывать.** Перед написанием кода и когда надо понять, где что лежит.
> Самое ценное здесь — раздел про индексы: там объяснено, почему каталог
> отвечал секунду ([09-decisions.md](09-decisions.md), D-81 и D-84).

<!-- TOC -->
<!-- Таблицу ниже собирает scripts/update_docs_toc.py — руками не править. -->

Всего строк: 652.

| Стр. | Раздел |
|---:|---|
| 45 | ## [Что где лежит (фактически)](#что-где-лежит-фактически) |
| 193 | ## [Стек](#стек) |
| 213 | ## [Структура](#структура) |
| 252 | ## [Схема БД](#схема-бд) |
| 298 | ### [Индексы](#индексы) |
| 364 | ### [Поисковый индекс](#поисковый-индекс) |
| 375 | ## [Реестр настроек — единая точка правды](#реестр-настроек--единая-точка-правды) |
| 398 | ### [Список настроек](#список-настроек) |
| 430 | ## [Ключевые сервисы](#ключевые-сервисы) |
| 432 | ### [`anime365.py` — единственная точка выхода в сеть](#anime365py--единственная-точка-выхода-в-сеть) |
| 437 | ### [`catalog_sync.py` — три режима](#catalogsyncpy--три-режима) |
| 446 | ### [`quality.py`](#qualitypy) |
| 452 | ### [`episode_type.py`](#episodetypepy) |
| 472 | ### [`filename_rules.py` + `config/filename_rules.yaml`](#filenamerulespy--configfilenamerulesyaml) |
| 508 | ### [`library_watcher.py` — слежение за файловой системой](#librarywatcherpy--слежение-за-файловой-системой) |
| 537 | ### [`downloader.py` + `postprocess.py`](#downloaderpy--postprocesspy) |
| 550 | ## [API (черновик)](#api-черновик) |
| 589 | ## [Многопользовательность](#многопользовательность) |
| 599 | ## [Требования к UI-вёрстке](#требования-к-ui-вёрстке) |
| 610 | ## [Автозагрузка субтитров (2026-08-10)](#автозагрузка-субтитров-2026-08-10) |
| 612 | ### [Таблицы](#таблицы) |
| 625 | ### [Модули](#модули) |
| 634 | ### [Маршруты](#маршруты) |

<!-- /TOC -->

## Что где лежит (фактически)

Раздел сверен с кодом 2026-08-01. Описания взяты из докстрок самих модулей —
если он снова разойдётся с реальностью, чинить надо здесь.

```
backend/app/
├─ main.py                      Точка входа FastAPI.
├─ core/                         # ядро: конфигурация, БД, безопасность
│  ├─ bootstrap.py                Инициализация БД при старте: таблицы, FTS, пользователь по умолчанию, слой настроек.
│  ├─ config.py                   Склейка трёх слоёв настроек: default (код) < ENV < БД (правка из UI).
│  ├─ db.py                       Подключение к SQLite.
│  ├─ errors.py                   Ошибки API с машинными кодами.
│  ├─ events.py                   Шина событий для живого обновления интерфейса.
│  ├─ logging_setup.py            Логирование с вырезанием секретов.
│  ├─ middleware.py               Заголовки безопасности, проверка сессии и защита от CSRF.
│  ├─ migrations.py               Лёгкие миграции схемы для SQLite.
│  ├─ paths.py                    Проверка путей записи.
│  ├─ permissions.py              Права доступа.
│  ├─ proxy.py                    Прокси для исходящих запросов.
│  ├─ security.py                 Пароль на вход, сессии и ограничитель попыток.
│  └─ settings_registry.py        Единый реестр настроек — единственная точка правды.
├─ models/                       # модели SQLAlchemy
│  ├─ catalog.py                  Модели каталога Anime365.
│  ├─ downloads.py                Очередь загрузок.
│  ├─ library.py                  Модели локальной библиотеки и маппинга.
│  └─ settings.py                 Служебные модели: пользователи и оверрайды настроек из UI.
├─ api/                          # роутеры
│  ├─ auth.py                     Вход в UI: один общий пароль, сессия в куке.
│  ├─ catalog.py                  Эндпоинты каталога.
│  ├─ deps.py                     Зависимости роутеров: ограничитель частоты для дорогих эндпоинтов.
│  ├─ downloads.py                Эндпоинты очереди загрузок.
│  ├─ events.py                   SSE-поток событий для интерфейса.
│  ├─ health.py                   Healthcheck для Docker и для быстрой проверки живости руками.
│  ├─ library.py                  Эндпоинты библиотеки: состояние, сканирование, нераспознанное, ручной маппинг.
│  ├─ maintenance.py              Служебные операции над кэшем.
│  ├─ settings.py                 Эндпоинты настроек.
│  ├─ stream.py                   Проксирование видео и субтитров для встроенного плеера.
│  ├─ title.py                    Эндпоинты страницы тайтла.
│  └─ users.py                    Учётные записи и права.
├─ services/                     # бизнес-логика
│  ├─ anime365.py                 Клиент Anime365 API — единственная точка выхода в сеть.
│  ├─ audio.py                    Извлечение звуковой дорожки из видео.
│  ├─ audio_library.py            Фонотека: что извлечено, где лежит и можно ли это слушать.
│  ├─ catalog_sync.py             Синхронизация каталога Anime365 в локальную БД.
│  ├─ coverage.py                 Сводка переводов по тайтлу: группировка, покрытие серий, оценка качества.
│  ├─ download_queue.py           Очередь загрузок: постановка задач, воркеры, постобработка.
│  ├─ downloader.py               Скачивание файлов с докачкой по Range.
│  ├─ filename_rules.py           Разбор имён файлов по правилам из config/filename_rules.yaml.
│  ├─ fts.py                      Полнотекстовый поиск по названиям (SQLite FTS5).
│  ├─ jellyfin.py                 Интеграция с Jellyfin: пинок на пересканирование и ссылка на страницу тайтла.
│  ├─ jellyfin_audit.py           Сверка нашей библиотеки с тем, что распознал Jellyfin.
│  ├─ languages.py                Приведение языковых кодов дорожек к двухбуквенным.
│  ├─ library_scan.py             Сканирование локальной библиотеки.
│  ├─ library_service.py          Сохранение результатов сканирования и автоматический маппинг.
│  ├─ library_watcher.py          Слежение за библиотекой через inotify (watchdog).
│  ├─ mapping_info.py             Сведения о привязке тайтла к локальным папкам — для плашки на странице тайтла.
│  ├─ matcher.py                  Сопоставление локальных папок с тайтлами Anime365.
│  ├─ naming.py                   Построение имён файлов для скачанного.
│  ├─ nfo_parser.py               Разбор .nfo, которые Jellyfin кладёт рядом с медиафайлами.
│  ├─ shikimori.py                Связи между тайтлами — порядок просмотра.
│  ├─ stream_probe.py             Определение звуковых дорожек через ffprobe — фоновой джобой.
│  ├─ title_service.py            Сборка данных для страницы тайтла.
│  ├─ translation_scan.py         Фоновый сбор сведений о том, какие переводы есть у тайтла на сайте.
│  └─ translation_types.py        Виды перевода: что за ними стоит и что из этого следует.
├─ workers/                      # фоновые задачи
│  └─ scheduler.py                Фоновые задачи.
```

```
frontend/src/
├─ pages/          # страницы
│  ├─ CatalogPage.tsx
│  ├─ LibraryPage.tsx
│  ├─ LoginPage.tsx
│  ├─ QueuePage.tsx
│  ├─ SettingsPage.tsx
│  └─ TitlePage.tsx
├─ components/     # компоненты
│  ├─ AudioLibraryTab.tsx
│  ├─ AudioTracks.tsx
│  ├─ BatchDownload.tsx
│  ├─ BindEpisodeModal.tsx
│  ├─ BindFolderModal.tsx
│  ├─ CatalogCard.tsx
│  ├─ CatalogFilters.tsx
│  ├─ CatalogGrid.tsx
│  ├─ CollectionButton.tsx
│  ├─ DisplaySettings.tsx
│  ├─ DownloadButton.tsx
│  ├─ EpisodeDownloadPanel.tsx
│  ├─ EpisodeRow.tsx
│  ├─ EpisodeTargetsAlert.tsx
│  ├─ EpisodeTargetsModal.tsx
│  ├─ ErrorState.tsx
│  ├─ Flag.tsx
│  ├─ JellyfinAudit.tsx
│  ├─ Logo.tsx
│  ├─ MapFolderModal.tsx
│  ├─ MappingPanel.tsx
│  ├─ MappingReport.tsx
│  ├─ OnlineSearchModal.tsx
│  ├─ PlayerModal.tsx
│  ├─ PosterPlaceholder.tsx
│  ├─ RelatedTitles.tsx
│  ├─ SyncIndicator.tsx
│  ├─ ThemeToggle.tsx
│  ├─ TitleAudioSection.tsx
│  ├─ TitleQuickLinks.tsx
│  ├─ TranslationGroups.tsx
│  └─ UsersPanel.tsx
├─ hooks/          # хуки
│  ├─ useAuth.ts
│  ├─ usePermissions.ts
│  ├─ useCatalogIds.ts
│  ├─ useCatalogUrlState.ts
│  ├─ useServerEvents.ts
│  ├─ useUnsavedGuard.ts
│  ├─ useVirtualCatalog.ts
│  └─ useVisibleWidthVar.ts
├─ api/            # клиент API
│  ├─ client.ts
│  ├─ downloads.ts
│  ├─ library.ts
│  ├─ title.ts
│  └─ users.ts
├─ stores/         # клиентские настройки
│  └─ preferences.ts
├─ i18n/           # локали ru/en/ja
│  ├─ apiError.ts
│  ├─ en.ts
│  ├─ flags.ts
│  ├─ format.ts
│  ├─ index.tsx
│  ├─ ja.ts
│  ├─ languages.ts
│  ├─ ru.ts
│  ├─ seriesType.ts
│  └─ titles.ts
└─ App.tsx             
└─ routes.tsx          Маршруты отдельным деревом, а не JSX внутри шапки
└─ main.tsx            
```

Чего нет отдельными модулями и не будет: `postprocess.py` (логика внутри
`download_queue`), `quality.py` и `episode_type.py` — их функции разошлись по
`coverage.py` и `downloader.py`.

## Стек

| Слой | Выбор | Почему |
|---|---|---|
| Backend | Python 3.12 + FastAPI + Uvicorn | 100–180 МБ RSS против 350–500 у Spring Boot; asyncio под I/O-профиль задачи |
| ORM/БД | SQLite (WAL) + SQLAlchemy 2.0 | 29k тайтлов ≈ 25 МБ, бэкап = копия файла |
| Поиск | SQLite FTS5 по `allTitles` | локальный мгновенный поиск по кэшу |
| Планировщик | APScheduler в процессе | без Celery/Redis |
| Очередь | таблица в SQLite + asyncio-воркеры | переживает рестарт, ретраи, пауза |
| HTTP-клиент | httpx (async) | HTTP/2, стриминг, Range-докачка |
| Frontend | React 18 + TS + Vite + Mantine | таблицы/гриды/модалки/тёмная тема из коробки |
| Данные на фронте | TanStack Query + TanStack Virtual | виртуализация 29k карточек |
| Прогресс | SSE (`/api/events`) | проще WebSocket, хватает для односторонних обновлений |
| Контейнер | один multi-stage образ | ~200 МБ, один порт, один процесс |

Фронт собирается в статику и раздаётся самим FastAPI через `StaticFiles` — второго контейнера
с Nginx нет.

---

## Структура

```
anime365-downloader/
├─ docker-compose.yml          # media-тома, config-том, порт
├─ Dockerfile                  # multi-stage: node build → python slim
├─ .env.example                # генерируется из реестра настроек
├─ claude/                     # рабочая документация (эта папка)
├─ docs/                       # user.md, admin.md, developer.md
├─ config/                     # монтируется наружу
│  ├─ settings.yaml            # оверрайды из UI
│  └─ filename_rules.yaml      # правила парсинга имён
├─ backend/
│  ├─ pyproject.toml
│  ├─ tests/
│  │  └─ filenames.yaml        # корпус регрессионных тестов парсера
│  └─ app/
│     ├─ main.py               # FastAPI, роутеры, StaticFiles, lifespan
│     ├─ core/
│     │  ├─ settings_registry.py  # ЕДИНЫЙ реестр настроек (см. ниже)
│     │  ├─ config.py             # склейка дефолт < ENV < БД
│     │  ├─ db.py                 # engine, session, WAL, миграции
│     │  ├─ security.py           # пароль, сессия, валидация путей и URL
│     │  └─ events.py             # шина событий → SSE
│     ├─ models/               # SQLAlchemy-модели
│     ├─ api/                  # роутеры
│     ├─ services/             # бизнес-логика (см. ниже)
│     └─ workers/              # запуск/остановка фоновых задач
└─ frontend/
   └─ src/
      ├─ pages/                # Catalog, Title, Queue, Library, Settings
      ├─ components/
      ├─ hooks/
      ├─ i18n/                 # ru / en / ja
      └─ stores/               # клиентские настройки в localStorage
```

---

## Схема БД

```
app_user(id, name)                      -- сейчас одна строка id=1
app_setting(key, value, updated_at)     -- ТОЛЬКО оверрайды из UI

series(id, titles_json, all_titles_json, type, type_title, year, season,
       number_of_episodes, poster_url, poster_url_small, url,
       mal_id, anilist_id, anidb_id, mal_score, is_airing, is_active, is_hentai,
       descriptions_json, links_json, synced_at)
series_fts(series_id, titles)           -- FTS5, все варианты названий
genre(id, title)
series_genre(series_id, genre_id)

episode(id, series_id, episode_int, episode_full, episode_title,
        episode_type, first_uploaded, is_active)

translation(id, series_id, episode_id, type, type_kind, type_lang,
            authors_summary, authors_json, quality_type,
            width, height, duration, is_active, priority, synced_at)
translation_embed(translation_id, has_subtitles, subtitles_url,
                  download_heights_json, fetched_at)   -- ленивый кэш embed

library_root(id, kind[series|movie], path, total_bytes, scanned_at)
library_folder(id, root_id, path, parent_id, season_number,
               nfo_json, size_bytes, mtime, scanned_at)
library_file(id, folder_id, filename, kind[video|subtitle|meta],
             lang, season_number, episode_int, episode_int_end,
             matched_rule_id, release_group, size_bytes, mtime,
             anime365_translation_id,      -- если файл от Anime365
             streamdetails_json)           -- дорожки из .nfo

title_mapping(id, series_id, folder_id, source, confidence, matched_on,
              is_manual, created_by_user_id, created_at)
episode_mapping(id, series_id, episode_int, file_id, role[video|sub_ru|sub_ja|sub_en],
                is_manual, created_by_user_id)

download_task(id, translation_id, series_id, episode_int, kind[video|subtitle],
              requested_height, resolved_height, target_path, tmp_path,
              status, attempts, last_error, bytes_done, bytes_total,
              speed, created_by_user_id, created_at, updated_at)

scan_run(id, kind[catalog_full|catalog_incr|catalog_airing|library|ffprobe],
         status, started_at, finished_at, stats_json, error)
```

### Индексы

Обычные, по одной колонке: `translation(series_id)`, `translation(episode_id)`,
`episode(series_id)`, `series(year)`, `series(type)`, `series(is_airing)`,
`title_mapping(series_id)`, `title_mapping(folder_id)`, `download_task(status)`.

**Составные покрывающие — для каталога.** Это не микрооптимизация, а разница между
секундами и долями миллисекунды, и вот почему.

Таблица `series` **широкая**: в каждой строке лежат `titles`, `all_titles`, `descriptions`
и `links` — вместе около 25 МБ на 29 тысяч строк. SQLite хранит строку целиком на страницах
таблицы, поэтому любое обращение к самой строке читает всю эту массу, даже когда нужен один
`id`. Замерено: `SELECT COUNT(*) FROM series WHERE is_hentai = 0` — **1229 мс**, тогда как
`COUNT(*)` без условий — 0,2 мс.

Запрос каталога делает два таких обращения: `COUNT` для полосы прокрутки и окно с сортировкой.

**Индексов ровно столько, сколько порядков сортировки в интерфейсе, и в каждом лежат все
фильтруемые колонки:**

```
ix_series_catalog_score  (is_hentai, mal_score, id, year, type, season_name, is_airing)
ix_series_catalog_year   (is_hentai, year,      id, mal_score, type, season_name, is_airing)
ix_series_catalog_id     (is_hentai, id, mal_score, year, type, season_name, is_airing)
ix_series_catalog_title  (is_hentai, title, id, mal_score, year, type, season_name, is_airing)
```

Устройство читается так: ведущая колонка — `is_hentai` (условие есть почти в каждом запросе, а
ведущая колонка обязана совпадать с условием, иначе индекс не применится); вторая — колонка
сортировки, чтобы порядок брался готовым; дальше `id` и **все остальные фильтруемые колонки**.
Последнее — самое важное: человек фильтрует по одному полю, а сортирует по другому, и если
фильтруемой колонки в индексе нет, SQLite дочитывает её из широкой таблицы. Именно так фильтр
по сезону с сортировкой по оценке стоил 4,7 с ([D-84](09-decisions.md)).

`title` лежит только в своём индексе: он втрое тяжелее остальных, а фильтра по названию нет —
поиск идёт через FTS и приходит списком идентификаторов.

Рядом: `series_genre (genre_id, series_id)` — первичный ключ идёт в обратном порядке, поэтому
без него отбор «все тайтлы жанра» просматривал таблицу целиком.

**Индекс называется явно.** Запросы каталога строятся с `INDEXED BY` по таблице `SORT_INDEX`
в `api/catalog.py`, а не отдаются на выбор планировщику: при равной оценке стоимости он иногда
берёт индекс, покрывающий фильтр, но не сортировку. Локальная SQLite 3.49 и контейнерная 3.46
на одних и тех же запросах выбирают разное, то есть цена вопроса — обновление системного пакета.

Цена набора: около 3 МБ на базе в 74 МБ и +8 % к стоимости записи. Последнее с лихвой
окупается сторожем изменений в `_upsert_series` (`ON CONFLICT … DO UPDATE … WHERE
<что-то отличается>`): суточный рескан перечитывает весь каталог, но переписывает только
изменившееся — пачка в тысячу неизменившихся тайтлов стоит 5 мс вместо 2,4 с.

⚠️ **Ловушки, все проверены замером** ([D-81](09-decisions.md), [D-84](09-decisions.md)):

* **лишний индекс делает медленнее.** Узкие `ix_series_catalog_airing` и `…_type` покрывали
  фильтр, но не сортировку, и планировщик выбирал их: с ними батарея стоит 211 мс, без них
  145 мс. Они удаляются при старте (`OBSOLETE_INDEXES`);
* **`ANALYZE` сделал хуже.** С собранной статистикой «жанр плюс сортировка по оценке» ушёл
  в скан: 2,1 мс → 377 мс. Поэтому не запускаем;
* **проверять надо всю батарею.** Неполный набор однажды увёл планировщик так, что сортировка
  по году деградировала с 0,3 мс до 3,3 с.

Индексы, добавленные или изменённые в моделях после создания таблиц, приводит в соответствие
`sync_indexes()` при старте: `create_all` создаёт их только вместе с новой таблицей, а индекс с
уже занятым именем не переопределяет вовсе. План запросов закреплён тестом
`tests/test_catalog_indexes.py` — он перебирает 60 сочетаний фильтров и сортировок и требует,
чтобы каждое обращение к `series` было покрывающим.

### Поисковый индекс

`series_fts` — FTS5 по всем вариантам названий. ⚠️ **`rowid` записи — это и есть `series.id`.**
Колонка `series_id` в таблице осталась, но читать её дорого: у FTS5 неиндексируемая колонка
лежит в содержимом записи. Замерено на 7525 совпадениях: `rowid` — 2,4 мс, `series_id` —
243 мс, а с `ORDER BY rank` — 459 мс. Ранжирование убрано вовсе: выдачу каталога сортирует
пользователь. Базы, собранные до этого правила, пересобираются одноразовой фоновой задачей
через полминуты после старта.

---

## Реестр настроек — единая точка правды

Каждая настройка объявляется **один раз**. Из реестра генерируются: парсинг ENV, форма в UI,
`.env.example`, валидаторы. Иначе три слоя (`дефолт < ENV < UI`) неизбежно разъедутся.

```python
Setting(
    key="max_concurrent_downloads",
    env="A365_MAX_CONCURRENT_DOWNLOADS",
    type=int, default=1,
    validator=lambda v: v >= 0,          # 0 = без ограничения
    group="downloads",
    secret=False,
    label_ru="Одновременных загрузок",
    help_ru="0 — без ограничения",
)
```

Эффективное значение = `БД (UI) → ENV → default`. API настроек возвращает для каждого поля
не только значение, но и `source` (`default` / `env` / `ui`), чтобы UI показал, откуда оно взято,
и предложил сброс. Секретные (`access_token`, пароль) наружу отдаются **только маской** и флагом
`is_set`.

### Список настроек

| Группа | Ключ | Дефолт |
|---|---|---|
| api | `base_url` | `https://smotret-anime.app` |
| api | `mirrors` (allowlist) | 4 известных домена |
| api | `access_token` 🔒 | — |
| api | `request_rate_limit` | 5 req/s |
| api | `user_agent` | `Anime365Downloader/1.0` |
| paths | `series_root` | `/media/anime` |
| paths | `movies_root` | `/media/anime_movies` |
| paths | `download_tmp` | `/data/tmp` |
| downloads | `max_concurrent_downloads` | 1 (0 = ∞) |
| downloads | `preferred_height` | 1080 |
| downloads | `max_attempts` | 5 |
| downloads | `retry_backoff_base` | 30 с |
| downloads | `min_free_space_gb` | 10 |
| downloads | `try_direct_cdn` | false |
| downloads | `speed_limit_kbps` | 0 (без лимита) |
| schedule | `catalog_refresh_hours` | 1 |
| schedule | `catalog_full_refresh_hours` | 24 |
| schedule | `library_scan_hours` | 6 |
| matching | `fuzzy_auto_threshold` | 88 |
| matching | `fuzzy_suggest_threshold` | 75 |
| files | `puid` / `pgid` / `umask` | 1000 / 1000 / 022 |
| security | `ui_password` 🔒 | — (пусто = вход открыт, с предупреждением) |
| features | `enable_ffprobe` | false |
| features | `enable_jellyfin` | false |
| features | `jellyfin_url` / `jellyfin_api_key` 🔒 | — |

---

## Ключевые сервисы

### `anime365.py` — единственная точка выхода в сеть
Список зеркал из allowlist, при ошибке/таймауте переход к следующему. Все ссылки из `embed`
прогоняются через `rewrite_host()` на текущий `base_url`. Общий семафор + минимальный интервал
между запросами. `User-Agent` обязателен (просьба из документации API).

### `catalog_sync.py` — три режима
| Режим | Что делает | Стоимость |
|---|---|---|
| `full` | полный скан по `afterId` | 44 запроса, 32 с |
| `incremental` | `afterId = max(known_id)`, ловит новые тайтлы | секунды |
| `airing` | `isAiring=1`, 306 тайтлов | секунды |

У series нет `updatedDateTime`, поэтому правки существующих тайтлов ловятся только полным рескaном.

### `quality.py`
- дешёвая оценка максимума — `height` из списочного запроса переводов (без `embed`);
- точные ступени — `embed.download`, лениво;
- `resolve(preferred, available)` → максимальная ступень ≤ `preferred`, иначе максимальная ниже,
  иначе единственная имеющаяся.

### `episode_type.py`
| `episodeType` | Куда |
|---|---|
| `preview`, `pv`, `cm`, `opening`, `ending`, `other`, `menu` | не контент, скрываем и не качаем |
| `tv`, `ona` | серии сезонной папки |
| `special`, `ova` | `Specials/` + `S00E{n}` (так их видит Jellyfin) |
| `movie` при `type=movie` у тайтла | корень фильмов, схема Radarr |
| `movie` внутри сериала (115 таких тайтлов) | остаётся серией в папке сериала |

Последний случай автоматически надёжно не развести → на странице тайтла переключатель
«считать фильмом / сериалом» с запоминанием.

Ещё две поправки, обе из живых данных ([D-143](09-decisions.md), [D-145](09-decisions.md)):

* **`isActive: 0` — заготовка**, а не серия. Сайт заводит пустые записи впрок,
  иногда на сезон вперёд. В список серий и в счёт «всего» они не идут.
* **Что считать «добавкой», зависит от типа тайтла.** У сериала добавка — это
  `special`/`ova`, у тайтла-OVA основное как раз `ova`. Иначе у OVA в основном
  списке не остаётся ничего.

### `filename_rules.py` + `config/filename_rules.yaml`
Регэкспы **не в коде**. Упорядоченный список правил, правится без пересборки образа.

```yaml
- id: jellyfin_sxxexx
  scope: series            # series | movie | any
  priority: 100
  pattern: '(?i)S(?P<season>\d{1,2})E(?P<episode>\d{1,4})(?:-E(?P<episode_end>\d{1,4}))?'
  enabled: true
- id: ohys_dash_number
  scope: series
  priority: 90
  pattern: '(?i)\s-\s(?P<episode>\d{1,3})\s*\('
- id: anime365_own
  scope: any
  priority: 95
  pattern: '(?i)\[Anime365\].*?-\s*(?P<episode>\d{1,3})\s*\(t(?P<translation_id>\d+)\)'
- id: movie_year
  scope: movie
  priority: 80
  pattern: '(?P<title>.+?)\s*\((?P<year>(19|20)\d{2})\)'
```

Механика:
- сортировка по `priority`, применяется **первое сматчившееся**, в результат пишется `matched_rule_id`
  (виден в UI и в отчёте — всегда понятно, каким правилом разобралось имя);
- контракт — только именованные группы (`season`, `episode`, `episode_end`, `title`, `year`,
  `translation_id`, `lang`, `group`); неизвестные игнорируются, поэтому правило добавляется
  без правки кода;
- в том же YAML: `exclude_patterns`, `language_suffixes`, `release_group_patterns`, `technical_noise`;
- **dry-run** `POST /api/library/parse-test` и CLI: принимают список имён (хоть весь `tree`),
  возвращают разбор и список нераспознанного — рабочий цикл «пользователь присылает список →
  дописываем правила»;
- корпус `backend/tests/filenames.yaml`: имя → ожидаемый разбор; новое правило не имеет права
  сломать уже разобранные имена.

### `library_watcher.py` — слежение за файловой системой

Основной механизм актуальности библиотеки; расписание оставлено как страховка ([D-20](09-decisions.md)).

```
watchdog Observer (рекурсивно по series_root и movies_root)
   → фильтр по расширениям (видео, субтитры, .nfo — остальное игнор)
   → буфер {папка: время последнего события}
   → дебаунс watch_debounce_seconds
   → пересканировать ТОЛЬКО эту папку
   → событие в SSE, UI обновляется без перезагрузки
```

Почему следим за каталогами, а не файлами: одна inotify-watch ≈ 1 КБ памяти ядра, каталогов
в библиотеке 1–2 тысячи → 1–2 МБ и нулевой CPU в простое.

Обязательная обработка отказов (оба тихие, поэтому проверяем явно):
- **сетевая ФС** (NFS/SMB) — события от других хостов не приходят вовсе;
- **`fs.inotify.max_user_watches`** — при упоре watchdog деградирует без внятной ошибки;
  читаем лимит из `/proc/sys/fs/inotify/max_user_watches`, сравниваем с числом каталогов,
  при нехватке не пытаемся стартовать.

В обоих случаях: откат на расписание + предупреждение в лог + **видимый статус в UI**
(пользователь должен знать, что живёт на расписании). Статус слежения (`active` / `fallback` +
причина) отдаётся в `/api/library/status`.

Дебаунс обязателен: копирование одного файла порождает десятки событий, а Jellyfin и Sonarr
дополнительно пишут `.nfo` и постеры рядом с видео.

### `downloader.py` + `postprocess.py`
```
queued → downloading (tmp/*.part, Range-докачка) → downloaded
       → postprocessing (rename → move → chown/chmod → reindex) → done
       ↘ failed (attempts++, экспоненциальный бэкофф) → queued
       ↘ paused / cancelled / already_exists
```
Постобработка — отдельный воркер, чтобы медленное перемещение между дисками не блокировало
скачивание. Прогресс и статусы льются в SSE. Перед скачиванием `embed` запрашивается заново —
подписанные ссылки живут недолго.

---

## API (черновик)

```
GET  /api/health
GET  /api/settings                      # значения + source + маски секретов
PUT  /api/settings
POST /api/settings/reset/{key}
POST /api/settings/test-token

GET  /api/catalog/ids?filters…          # массив id для виртуального списка
POST /api/catalog/cards                 # окно карточек по списку id
GET  /api/catalog/genres
GET  /api/catalog/search?query=         # онлайн-поиск через API сайта
POST /api/catalog/sync                  # ручной запуск

GET  /api/series/{id}
GET  /api/series/{id}/translations      # сводка + покрытие диапазонами
GET  /api/series/{id}/quality-summary
GET  /api/translations/{id}/embed       # ленивый, с кэшем

POST /api/downloads                     # одна задача
POST /api/downloads/batch               # батч
GET  /api/downloads
POST /api/downloads/{id}/{retry|pause|resume|cancel}
POST /api/downloads/pause-all

GET  /api/library/roots                 # + размеры
POST /api/library/scan
GET  /api/library/unmatched
POST /api/library/parse-test            # dry-run парсера имён
POST /api/mapping/title
DELETE /api/mapping/title/{id}
POST /api/mapping/episode

GET  /api/events                        # SSE: прогресс загрузок, статусы джоб
```

---

## Многопользовательность

Аккаунтов нет (один общий пароль), но схема готова: `app_user` с единственной строкой,
`created_by_user_id` у пользовательских сущностей, клиентские настройки под версионированным
префиксом в `localStorage`. SQLite в WAL спокойно держит 2–3 человека. Все изменяющие операции
идемпотентны по ключу (`translation_id + kind + height`), поэтому два человека, нажавшие «скачать»
на одном переводе, не создадут две задачи.

---

## Требования к UI-вёрстке

Динамические элементы не должны вылезать за свои границы или прятаться:
- значки на постере — grid/flex с `min-height` под максимальное число бейджей, переносом
  и `overflow: hidden`;
- длинные названия — `line-clamp` с фиксированной высотой блока, чтобы карточки не «прыгали»;
- проверка на 375 / 768 / 1280 px и на карточке с максимумом бейджей одновременно;
- блоки переводов рендерятся только при раскрытии аккордеона (у тайтла бывает ~2000 переводов).

---

## Автозагрузка субтитров (2026-08-10)

### Таблицы

| Таблица | Что хранит |
|---|---|
| `subtitle_author` | группы перевода, встреченные среди субтитров: язык, нормализованный ключ, показываемое имя, счётчик |
| `autodownload_rule` | общая настройка **на язык**: режим, список приоритетов, чёрный список, три переключателя, отметка времени включения |
| `series_autodownload` | то же для конкретного тайтла; перекрывает общую |
| `autodownload_miss` | серии, к которым субтитров не нашлось: дата первого промаха, счётчик попыток, признак «выкинуто вручную» |

Списки хранятся JSON-массивами: это именно списки, а не связи — имя может быть
вписано руками и не встречаться в каталоге вовсе. Запись с приставкой `re:` —
регулярное выражение.

### Модули

| Модуль | Ответственность |
|---|---|
| `services/subtitle_authors.py` | нормализация имён групп, каталог, досбор, сверка записи со строкой |
| `services/autodownload_rules.py` | сложение общей настройки с настройкой тайтла, таблица участия |
| `services/autodownload.py` | отбор серий, ранжирование кандидатов, очередь промахов, постановка в общую очередь загрузок |
| `api/autodownload.py` | настройки, подсказка групп, список настроенных тайтлов, промахи, запуск |

### Маршруты

Все под правом `downloads`.

```
GET    /api/autodownload                  настройки всех языков
PUT    /api/autodownload/{lang}           сохранить настройку языка
GET    /api/autodownload/authors?lang=    подсказка групп
GET    /api/autodownload/configured       тайтлы со своей настройкой
GET    /api/autodownload/series/{id}      настройка тайтла и действующая
PUT    /api/autodownload/series/{id}/{lang}
DELETE /api/autodownload/series/{id}/{lang}
POST   /api/autodownload/run?only_configured=
POST   /api/autodownload/series/{id}/run
GET    /api/autodownload/misses
DELETE /api/autodownload/misses/{id}
POST   /api/autodownload/misses/retry
```
