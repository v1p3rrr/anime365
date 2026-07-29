# Anime 365 API — документация

Публичное API каталога аниме: список аниме, эпизоды, переводы (озвучки и субтитры) и ссылки на видео для просмотра/скачивания.

Источник: `https://smotret-anime.org/api-docs` · версия `v1.0`

---

## Общая информация

### Базовый адрес

Все методы находятся под `/api`. Основной сервер:

```
https://smotret-anime.app/api
```

Например: `https://smotret-anime.app/api/series`. Запросы можно отправлять и на тот домен, с которого открыта документация (относительный путь `/api`).

Просьба указывать в заголовке `User-Agent` название вашего сайта или программы.

### Формат ответа

Успешный ответ всегда обёрнут в поле `data`:

```json
{
  "data": {
    "id": 19730,
    "...": "..."
  }
}
```

Методы-списки возвращают массив в `data`. В случае ошибки вместо `data` возвращается объект `error` (схема `Error`).

Модификаторы, применимые к любому запросу:

- `pretty=1` — «читабельный» (отформатированный) JSON.
- `callback=<имя>` — вернуть JSONP.

### Авторизация

Каталог, эпизоды и метаданные переводов **открыты** и не требуют авторизации. Данные пользователя и ссылки на видео (embed) требуют параметр `access_token`.

Как получить токен:

1. Зарегистрируйте приложение на странице создания API-клиента — вы получите идентификатор `app`. Идентификатор `app` **не секрет**: его можно встраивать в публичные приложения и выкладывать в открытый исходный код. Секретен только `access_token`, привязанный к конкретному пользователю.
2. Обменяйте логин и пароль на токен: `GET /api/login?app=ВАШ_APP&email=...&password=...`, либо, если пользователь уже вошёл на сайте, получите токен по `GET /api/accessToken?app=ВАШ_APP`. Войти можно и через социальные сети на `/users/login`.
3. Передавайте токен в защищённых запросах параметром `?access_token=...`, например `/api/translations/embed/2423373?access_token=ТОКЕН`.

У токена **нет срока действия** — он работает бессрочно (до смены пароля).

### Постраничный вывод и полное сканирование

`limit` и `offset` регулируют количество элементов «на странице» и смещение. При полном сканировании больших списков (например всех переводов) **не используйте `offset`** — он работает очень медленно на сотнях тысяч записей. Используйте `afterId` (вернёт записи с `id` больше указанного):

```
GET /api/translations/?feed=id&afterId=10000
```

---

## Эндпоинты

### Раздел «Каталог»

| Метод | Путь | Назначение | Авторизация |
|---|---|---|---|
| GET | `/api/series` | Список аниме | нет |
| GET | `/api/series/{id}` | Аниме по ID | нет |
| GET | `/api/episodes` | Список эпизодов | нет |
| GET | `/api/episodes/{id}` | Эпизод по ID | нет |
| GET | `/api/videos/{id}` | Видео по ID | нет |

### Раздел «Переводы»

| Метод | Путь | Назначение | Авторизация |
|---|---|---|---|
| GET | `/api/translations` | Список переводов | нет |
| GET | `/api/translations/{id}` | Перевод по ID | нет |
| GET | `/api/translations/embed/{id}` | Видео/субтитры для просмотра и скачивания | **токен + подписка** |
| GET | `/api/upload/endpoints` | Точки загрузки видео (tus) | нет |
| POST | `/api/translations/create` | Добавить перевод из загруженного файла | **токен** |

### Раздел «Аккаунт»

| Метод | Путь | Назначение | Авторизация |
|---|---|---|---|
| GET | `/api/me` | Текущий пользователь | опционально |
| GET | `/api/login` | Получить access_token по логину и паролю | нет |
| GET | `/api/accessToken` | Получить access_token для текущей сессии | сессия на сайте |

---

## Каталог

### GET `/api/series` — Список аниме

Возвращает список аниме. Поддерживает поиск по названию (`query`), расширенный фильтр как на сайте (`chips`) и фильтрацию по точным значениям полей.

- Расширенный фильтр (тот же, что на странице `/catalog/filter/...`): `GET /api/series?chips=genre@=8,35;genre_op=and`
- Только нужные поля: `GET /api/series?fields=id,titles,typeTitle,posterUrlSmall`

**Query-параметры**

| Параметр | Тип | Описание |
|---|---|---|
| `afterId` | integer | Вернуть только аниме с `id` больше указанного. |
| `chips` | string | Строка расширенного фильтра каталога. |
| `fields` | string | Список нужных полей через запятую, чтобы уменьшить ответ. |
| `isActive` | integer | Допустимые значения: `0`, `1`. |
| `isAiring` | integer | Онгоинг (выходит прямо сейчас). Допустимые значения: `0`, `1`. |
| `limit` | integer | Размер страницы (по умолчанию `50`, максимум `1000`). |
| `myAnimeListId` | integer | — |
| `offset` | integer | Сколько записей пропустить (медленно для больших списков, см. `afterId`). По умолчанию `0`. |
| `order` | string | Значение `id` сортирует по возрастанию `id`. Допустимое значение: `id`. |
| `query` | string | Поиск по названию. |
| `season` | string | — |
| `type` | string | Тип: `tv`, `movie`, `ova`, `ona`, `special`, `music`. |
| `year` | integer | — |

**Ответ `200`** — список аниме. Состав полей объекта зависит от параметра `fields`.

```json
{
  "data": [
    {
      "id": 0,
      "myAnimeListId": 0,
      "myAnimeListScore": 0,
      "worldArtScore": 0,
      "numberOfEpisodes": 0,
      "season": "string",
      "year": 0,
      "type": "string",
      "typeTitle": "string",
      "isActive": 0,
      "isAiring": 0,
      "isHentai": 0,
      "posterUrl": "string",
      "posterUrlSmall": "string",
      "titles": {
        "property1": "string",
        "property2": "string"
      },
      "links": {}
    }
  ]
}
```

> `titles` — словарь `{ язык: название }`.

---

### GET `/api/series/{id}` — Аниме по ID

Информация о конкретном аниме и список его эпизодов.

**Path-параметры**

| Параметр | Тип | Обяз. | Описание |
|---|---|---|---|
| `id` | integer | да | — |

**Query-параметры**

| Параметр | Тип | Описание |
|---|---|---|
| `fields` | string | Список нужных полей через запятую. |

**Ответы:** `200`, `404`.

**Ответ `200`** — одно аниме (состав полей зависит от `fields`):

```json
{
  "data": {
    "id": 0,
    "myAnimeListId": 0,
    "myAnimeListScore": 0,
    "worldArtScore": 0,
    "numberOfEpisodes": 0,
    "season": "string",
    "year": 0,
    "type": "string",
    "typeTitle": "string",
    "isActive": 0,
    "isAiring": 0,
    "isHentai": 0,
    "posterUrl": "string",
    "posterUrlSmall": "string",
    "titles": {
      "property1": "string",
      "property2": "string"
    },
    "links": {}
  }
}
```

---

### GET `/api/episodes` — Список эпизодов

**Query-параметры**

| Параметр | Тип | Описание |
|---|---|---|
| `episodeInt` | number | Номер эпизода. |
| `episodeType` | string | Допустимые значения: `tv`, `movie`, `ova`, `ona`, `special`. |
| `fields` | string | Список нужных полей через запятую. |
| `isActive` | integer | Допустимые значения: `0`, `1`. |
| `isFirstUploaded` | integer | Допустимые значения: `0`, `1`. |
| `limit` | integer | Размер страницы (по умолчанию `50`, максимум `1000`). |
| `offset` | integer | Смещение (по умолчанию `0`; медленно для больших списков). |
| `seriesId` | integer | — |

**Ответ `200`** — список эпизодов:

```json
{
  "data": [
    {
      "id": 0,
      "seriesId": 0,
      "episodeFull": "string",
      "episodeInt": 0,
      "episodeTitle": "string",
      "episodeType": "string",
      "firstUploadedDateTime": "string",
      "isActive": 0,
      "isFirstUploaded": 0
    }
  ]
}
```

---

### GET `/api/episodes/{id}` — Эпизод по ID

Информация о конкретном эпизоде и список переводов.

**Path-параметры**

| Параметр | Тип | Обяз. |
|---|---|---|
| `id` | integer | да |

**Query-параметры**

| Параметр | Тип | Описание |
|---|---|---|
| `fields` | string | Список нужных полей через запятую. |

**Ответы:** `200`, `404`.

**Ответ `200`** — один эпизод:

```json
{
  "data": {
    "id": 0,
    "seriesId": 0,
    "episodeFull": "string",
    "episodeInt": 0,
    "episodeTitle": "string",
    "episodeType": "string",
    "firstUploadedDateTime": "string",
    "isActive": 0,
    "isFirstUploaded": 0
  }
}
```

---

### GET `/api/videos/{id}` — Видео по ID

**Path-параметры**

| Параметр | Тип | Обяз. |
|---|---|---|
| `id` | integer | да |

**Query-параметры**

| Параметр | Тип | Описание |
|---|---|---|
| `fields` | string | Список нужных полей через запятую. |

**Ответы:** `200`, `404`.

**Ответ `200`** — одно видео:

```json
{
  "data": {
    "id": 0,
    "episodeId": 0,
    "seriesId": 0,
    "filename": "string",
    "type": "string",
    "typeKind": "string",
    "typeLang": "string",
    "urlList": [
      "string"
    ]
  }
}
```

---

## Переводы

Перевод — это одна озвучка или субтитры для одного эпизода.

### GET `/api/translations` — Список переводов

Готовые сценарии:

- Последние добавленные: `GET /api/translations/`
- Переводы онгоингов: `GET /api/translations/?feed=recent`
- Все переводы (сначала самые старые, удобно для полного сканирования): `GET /api/translations/?feed=id` (или `feed=all`, включая неактивные)
- Все переводы аниме: `GET /api/translations/?seriesId=19730&limit=10000&fields=id,episode,authorsSummary,type`

Для полного сканирования используйте `afterId`, а не `offset`.

**Query-параметры**

| Параметр | Тип | Описание |
|---|---|---|
| `afterId` | integer | — |
| `episodeId` | integer | — |
| `feed` | string | Лента / сортировка: `recent` (онгоинги), `id`, `all` (включая неактивные), `updatedDateTime`, `addedDateTime`. |
| `fields` | string | Список нужных полей через запятую. |
| `isActive` | integer | Допустимые значения: `0`, `1`. |
| `limit` | integer | Размер страницы (по умолчанию `50`, максимум `1000`). |
| `offset` | integer | Смещение (по умолчанию `0`; медленно для больших списков). |
| `qualityType` | string | — |
| `seriesId` | integer | — |
| `type` | string | Тип перевода, например `voiceRu`, `subRu`. |

**Ответ `200`** — список переводов:

```json
{
  "data": [
    {
      "id": 0,
      "seriesId": 0,
      "addedDateTime": "string",
      "activeDateTime": "string",
      "updatedDateTime": "string",
      "authorsList": [
        "string"
      ],
      "fansubsTranslationId": 0,
      "isActive": 0,
      "priority": 0,
      "qualityType": "string",
      "type": "string",
      "typeKind": "string",
      "typeLang": "string",
      "title": "string"
    }
  ]
}
```

---

### GET `/api/translations/{id}` — Перевод по ID

**Path-параметры**

| Параметр | Тип | Обяз. |
|---|---|---|
| `id` | integer | да |

**Query-параметры**

| Параметр | Тип | Описание |
|---|---|---|
| `fields` | string | Список нужных полей через запятую. |

**Ответы:** `200`, `404`.

**Ответ `200`** — один перевод:

```json
{
  "data": {
    "id": 0,
    "seriesId": 0,
    "addedDateTime": "string",
    "activeDateTime": "string",
    "updatedDateTime": "string",
    "authorsList": [
      "string"
    ],
    "fansubsTranslationId": 0,
    "isActive": 0,
    "priority": 0,
    "qualityType": "string",
    "type": "string",
    "typeKind": "string",
    "typeLang": "string",
    "title": "string"
  }
}
```

---

### GET `/api/translations/embed/{id}` — Видео для просмотра/скачивания 🔒

Возвращает информацию для воспроизведения и скачивания перевода (ссылки на видео и субтитры). Большинство субтитров софтсабом, т.е. нужно подключать ASS-субтитры из поля `subtitlesUrl`.

**Требует `access_token` от аккаунта с активной подпиской.**

Для онлайн-просмотра в HTML5-плеере используйте страницу `/translations/embed/{id}?access_token=ТОКЕН` (без префикса `/api`).

- Security: API Key

**Path-параметры**

| Параметр | Тип | Обяз. |
|---|---|---|
| `id` | integer | да |

**Query-параметры**

| Параметр | Тип | Описание |
|---|---|---|
| `access_token` | string | Токен доступа (см. раздел «Авторизация»). |

**Ответы:** `200`, `403`, `404`.

**Ответ `200`** — данные для воспроизведения (в документации тело показано как открытый объект; фактический состав полей включает ссылки на видео и `subtitlesUrl`):

```json
{
  "data": {}
}
```

> Примечание: в Swagger-документации набор полей `data` для этого метода не расписан явно (показан как `{}`), но по описанию он содержит ссылки на видео и поле `subtitlesUrl` для ASS-субтитров. Точные имена полей стоит проверить реальным запросом с валидным токеном.

---

### GET `/api/upload/endpoints` — Точки загрузки видео (tus)

Возвращает список доступных tus 1.0.0-эндпоинтов для загрузки видео (с метками, как в селекторе канала загрузки на сайте) и рекомендуемый для вас канал. Рекомендация зависит от вашей страны (CDN/РФ), как и на сайте.

Загрузите файл по tus на выбранный `tusUrl`, затем передайте его `serverId` (вместе с uuid/именем/размером) в `/api/translations/create`.

**Ответ `200`** — доступные точки загрузки:

```json
{
  "data": {
    "recommendedId": "2",
    "endpoints": [
      {
        "id": "2",
        "label": "CDN",
        "serverId": 28,
        "tusUrl": "string",
        "tusUrls": [
          "string"
        ],
        "recommended": true
      }
    ]
  }
}
```

> `recommendedId` — `id` рекомендуемого канала из списка `endpoints`.

---

### POST `/api/translations/create` — Добавить перевод из загруженного файла 🔒

Создаёт новый перевод из заранее загруженного видеофайла (и при необходимости файла субтитров). Доступно любому пользователю с действующим `access_token` — так же, как форма добавления на сайте.

**Загрузка файла (tus)**

Сначала получите список точек загрузки через `GET /api/upload/endpoints` и загрузите видео по протоколу tus 1.0.0 на рекомендованный `tusUrl` (например `https://time28.anime-on.ru/tus.php`, это `serverId 28`):

1. `POST /tus.php` с заголовками `Tus-Resumable: 1.0.0`, `Upload-Length: <размер в байтах>` и `Upload-Metadata: filename <base64(имя файла)>`. В ответе `201` придёт `Location: /tus.php?id=<uuid>`, а также заголовки `X-Upload-Uuid` и `X-Upload-Server-Id`.
2. `PATCH /tus.php?id=<uuid>` чанками с `Content-Type: application/offset+octet-stream`, `Tus-Resumable: 1.0.0` и `Upload-Offset: <текущее смещение>`, пока `Upload-Offset` не сравняется с `Upload-Length`. Можно возобновлять прерванную загрузку: `HEAD /tus.php?id=<uuid>` вернёт текущий `Upload-Offset`.

Затем вызовите этот метод, передав `serverId`, `videoUuid`, `videoFilename` и `videoSize`. Файл субтитров (если есть) загружается так же и передаётся в полях `subUuid` / `subFilename` / `subSize`.

- Security: API Key

**Query-параметры**

| Параметр | Тип | Описание |
|---|---|---|
| `access_token` | string | Токен доступа (см. раздел «Авторизация»). |

**Тело запроса** (`application/json`)

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `seriesId` | integer | да | ID аниме. Пример: `8245`. |
| `episodeNumber` | number | да | Номер серии. Пример: `3`. |
| `episodeType` | string | да | Тип серии (`tv`, `movie`, `ova`, `ona`, `special`, …). |
| `type` | string | да | Тип перевода (`voiceRu`, `subRu`, `voiceEn`, `subEn`, `raw`, …). |
| `authors` | string | нет | Ники авторов перевода (без ссылок). Пример: `Author1 & Author2`. |
| `addedByAuthor` | integer | нет | `1`, если вы участник команды авторов. Допустимые значения: `0`, `1`. |
| `serverId` | integer | да | ID сервера загрузки (из `X-Upload-Server-Id`, например `28`). |
| `videoUuid` | string | да | uuid загруженного видео (из tus). |
| `videoFilename` | string | да | Имя видеофайла. Пример: `episode-03.mp4`. |
| `videoSize` | integer | да | Размер видеофайла в байтах. |
| `videoSha256` | string | нет | sha256 видео в hex (проверка целостности). |
| `subUuid` | string | нет | uuid загруженных субтитров. |
| `subFilename` | string | нет | Имя файла субтитров. Пример: `episode-03.ass`. |
| `subSize` | integer | нет | Размер файла субтитров в байтах. |
| `subSha256` | string | нет | sha256 субтитров в hex. |

**Пример тела запроса**

```json
{
  "seriesId": 8245,
  "episodeNumber": 3,
  "episodeType": "tv",
  "type": "voiceRu",
  "authors": "Author1 & Author2",
  "serverId": 28,
  "videoUuid": "9e93fde2-604d-499c-8948-3afce1194c12",
  "videoFilename": "episode-03.mp4",
  "videoSize": 734003200,
  "videoSha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

**Пример cURL**

```bash
curl --request POST \
  --url 'https://smotret-anime.app/api/translations/create?access_token=123' \
  --header 'Accept: application/json' \
  --header 'Content-Type: application/json' \
  --data '{
    "seriesId": 8245,
    "episodeNumber": 3,
    "episodeType": "tv",
    "type": "voiceRu",
    "authors": "Author1 & Author2",
    "serverId": 28,
    "videoUuid": "9e93fde2-604d-499c-8948-3afce1194c12",
    "videoFilename": "episode-03.mp4",
    "videoSize": 734003200,
    "videoSha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  }'
```

**Ответы:** `200`, `400`, `403`.

**Ответ `200`** — перевод создан:

```json
{
  "data": {
    "id": 2531902,
    "seriesId": 8245,
    "episodeId": 184530,
    "addedDateTime": "2026-06-19 12:41:03",
    "activeDateTime": "",
    "updatedDateTime": "2026-06-19 12:41:03",
    "authorsList": [
      "Author1",
      "Author2"
    ],
    "authorsSummary": "Author1 & Author2",
    "fansubsTranslationId": 0,
    "isActive": 0,
    "priority": 0,
    "qualityType": "tv",
    "type": "voiceRu",
    "typeKind": "voice",
    "typeLang": "ru",
    "title": "Author1 & Author2",
    "url": "https://smotret-anime.app/translations/2531902"
  }
}
```

---

## Аккаунт

### GET `/api/me` — Текущий пользователь

Возвращает аккаунт, связанный с переданным `access_token` (или с текущей сессией на сайте). Для гостей поле `isLogined` равно `false`. Авторизация опциональна.

- Security: API Key

**Query-параметры**

| Параметр | Тип | Описание |
|---|---|---|
| `access_token` | string | Токен доступа (см. раздел «Авторизация»). |

**Ответ `200`** — текущий пользователь:

```json
{
  "data": {
    "isLogined": true,
    "id": 0,
    "name": "string",
    "isPremium": true,
    "premiumUntil": "string"
  }
}
```

---

### GET `/api/login` — Получить access_token по логину и паролю

Обменивает e-mail и пароль аккаунта на `access_token` для приложения `app`. `app` должен быть зарегистрированным клиентом — создайте его на странице создания API-клиента.

**Query-параметры**

| Параметр | Тип | Обяз. | Описание |
|---|---|---|---|
| `app` | string | да | Идентификатор вашего приложения. |
| `email` | string | да | — |
| `password` | string (password) | да | — |

**Ответы:** `200`, `403`.

**Ответ `200`** — новый токен:

```json
{
  "data": {
    "access_token": "string"
  }
}
```

---

### GET `/api/accessToken` — Получить access_token для текущей сессии

Выдаёт `access_token` для пользователя, вошедшего на сайте, для приложения `app`. Удобно из браузера, когда пользователь уже авторизован (в том числе через социальные сети).

**Query-параметры**

| Параметр | Тип | Обяз. | Описание |
|---|---|---|---|
| `app` | string | да | Идентификатор вашего приложения. |

**Ответы:** `200`, `403`.

**Ответ `200`** — новый токен:

```json
{
  "data": {
    "access_token": "string"
  }
}
```

---

## Схемы (SCHEMAS)

В документации перечислены схемы: `Error`, `Series`, `Episode`, `TranslationCreateRequest`, `Translation`, `Video`, `User`, `AccessTokenResponse`, `GenericObjectResponse`. Их поля совпадают с телами ответов соответствующих методов выше.

---

## Как это ложится на мини-приложение

Задача: просматривать список аниме, искать тайтлы, выбирать тип перевода и скачивать видео и/или субтитры.

| Шаг в приложении | Метод API |
|---|---|
| Список / поиск аниме | `GET /api/series?query=...&limit=...&fields=id,titles,posterUrlSmall,typeTitle` |
| Карточка тайтла + эпизоды | `GET /api/series/{id}` или `GET /api/episodes?seriesId={id}` |
| Список переводов эпизода (по типу) | `GET /api/translations?episodeId={id}&type=voiceRu` (или `subRu`, и т.д.) |
| Логин пользователя | `GET /api/login?app=...&email=...&password=...` → `access_token` |
| Проверка подписки | `GET /api/me?access_token=...` → поле `isPremium` |
| Ссылки на видео и субтитры | `GET /api/translations/embed/{translationId}?access_token=...` |

### Важные ограничения

1. **Скачивание требует премиум-подписки.** Ссылки на видео и субтитры отдаёт только `translations/embed/{id}`, и он требует `access_token` от аккаунта с активной подпиской. Каталог, эпизоды и метаданные переводов при этом открыты. Заложите в приложение экран авторизации и проверку `isPremium` через `/api/me`.
2. **Субтитры отдельным файлом.** Большинство субтитров — софтсаб (ASS), их нужно брать из поля `subtitlesUrl` в ответе `embed` и подключать/скачивать отдельно от видео.
3. **Точный формат ответа `embed` не задокументирован** (в Swagger показан как `{}`). Имена полей со ссылками стоит уточнить реальным запросом с валидным токеном перед вёрсткой логики скачивания.
4. **`User-Agent`.** Указывайте название приложения в заголовке запросов.
5. **Регистрация `app`.** Для `login` / `accessToken` нужен зарегистрированный идентификатор приложения (создаётся на `/api-clients`). Сам `app` не секретен, секретен только пользовательский `access_token`.

### Вывод

Да, API покрывает весь заявленный сценарий: просмотр каталога, поиск, фильтрацию переводов по типу и получение ссылок на видео и субтитры для скачивания. Единственное существенное условие — скачивание завязано на авторизованного пользователя с активной подпиской.
