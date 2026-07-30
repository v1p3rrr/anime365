# Архитектура

> Состояние на 2026-07-30. Ниже описано то, что **реально написано**; чего ещё нет — помечено.

## Что где лежит (фактически)

```
backend/app/
├─ core/
│  ├─ settings_registry.py   ✅ единый реестр настроек
│  ├─ config.py              ✅ склейка дефолт < ENV < UI
│  ├─ db.py                  ✅ SQLite WAL
│  ├─ migrations.py          ✅ дописывание недостающих колонок
│  ├─ bootstrap.py           ✅ инициализация при старте
│  └─ logging_setup.py       ✅ вырезание секретов из логов
├─ models/
│  ├─ catalog.py             ✅ series, episode, translation, embed-кэш, scan_run
│  ├─ library.py             ✅ корни, папки, файлы, маппинг тайтлов и эпизодов
│  ├─ downloads.py           ✅ очередь задач
│  └─ settings.py            ✅ app_user, app_setting
├─ services/
│  ├─ anime365.py            ✅ клиент API, зеркала, rewrite хоста
│  ├─ catalog_sync.py        ✅ три режима синхронизации
│  ├─ fts.py                 ✅ полнотекстовый поиск
│  ├─ coverage.py            ✅ сводка переводов, диапазоны, качество
│  ├─ title_service.py       ✅ сборка страницы тайтла
│  ├─ filename_rules.py      ✅ разбор имён по YAML-правилам
│  ├─ nfo_parser.py          ✅ разбор всех видов .nfo
│  ├─ matcher.py             ✅ каскады сопоставления
│  ├─ library_scan.py        ✅ обход файловой системы
│  ├─ library_service.py     ✅ сохранение результатов + маппинг
│  ├─ library_watcher.py     ✅ слежение через inotify
│  ├─ mapping_info.py        ✅ плашка привязки, покрытие, основная папка
│  ├─ naming.py              ✅ построение имён файлов
│  ├─ downloader.py          ✅ докачка по Range, allowlist хостов
│  └─ download_queue.py      ✅ воркеры, ретраи, постобработка
├─ api/                      ✅ health, catalog, title, library, downloads, maintenance
│                            ⬜ settings, events (SSE)
└─ workers/scheduler.py      ✅ APScheduler + запуск слежения

frontend/src/
├─ pages/
│  ├─ CatalogPage.tsx        ✅
│  ├─ TitlePage.tsx          ✅ (кнопки загрузки — ⬜)
│  ├─ LibraryPage.tsx        ✅
│  ├─ QueuePage.tsx          ⬜ заглушка
│  └─ SettingsPage.tsx       ⬜ заглушка
├─ components/               ✅ карточка, сетка, фильтры, сводка переводов,
│                               плашка привязки, модалки привязки, показ ошибок
├─ api/                      ✅ client, title, library
└─ stores/preferences.ts     ✅ настройки отображения в localStorage
```

Чего нет вовсе: `postprocess.py` отдельным модулем (логика внутри `download_queue`),
`core/events.py` и SSE, `core/security.py` (пароль, CSP, CSRF), `quality.py` и
`episode_type.py` (их функции разошлись по `coverage.py` и `downloader.py`).

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

Индексы: `translation(series_id)`, `translation(episode_id)`, `episode(series_id)`,
`series(year)`, `series(type)`, `series(is_airing)`, `title_mapping(series_id)`,
`title_mapping(folder_id)`, `download_task(status)`.

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
| `preview`, `pv`, `cm` | не контент, скрываем и не качаем |
| `tv`, `ona` | серии сезонной папки |
| `special`, `ova` | `Specials/` + `S00E{n}` (так их видит Jellyfin) |
| `movie` при `type=movie` у тайтла | корень фильмов, схема Radarr |
| `movie` внутри сериала (115 таких тайтлов) | остаётся серией в папке сериала |

Последний случай автоматически надёжно не развести → на странице тайтла переключатель
«считать фильмом / сериалом» с запоминанием.

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
