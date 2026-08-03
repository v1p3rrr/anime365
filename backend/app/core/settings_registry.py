"""Единый реестр настроек — единственная точка правды.

Каждая настройка объявляется здесь ровно один раз. Из реестра генерируются:
  * парсинг переменных окружения,
  * форма настроек в UI (label/help/группа/тип),
  * `.env.example`,
  * валидация значений.

Приоритет значений: default (код)  <  ENV  <  БД (правка из UI).
Если объявлять настройку в нескольких местах, три слоя неизбежно разъедутся,
поэтому добавлять новую настройку можно только сюда.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Callable


class SettingType(str, Enum):
    STR = "str"
    INT = "int"
    BOOL = "bool"
    PATH = "path"
    ENUM = "enum"
    STR_LIST = "str_list"


class SettingGroup(str, Enum):
    API = "api"
    PATHS = "paths"
    DOWNLOADS = "downloads"
    SCHEDULE = "schedule"
    MATCHING = "matching"
    FILES = "files"
    SECURITY = "security"
    FEATURES = "features"


class ValidationError(ValueError):
    """Значение настройки не прошло проверку."""


@dataclass(frozen=True)
class Setting:
    key: str
    type: SettingType
    default: Any
    group: SettingGroup
    label_ru: str
    help_ru: str = ""
    # Секретные значения наружу отдаются только маской и флагом is_set.
    secret: bool = False
    # Меняется только на сервере (ENV/код). Правка из UI отклоняется —
    # так устроен allowlist зеркал: иначе подмена значения в браузере даёт SSRF.
    server_only: bool = False
    # Допустимые значения для ENUM.
    choices: tuple[str, ...] = ()
    # Дополнительная проверка: возвращает None или текст ошибки.
    check: Callable[[Any], str | None] | None = None

    @property
    def env(self) -> str:
        return f"A365_{self.key.upper()}"

    def parse(self, raw: str) -> Any:
        """Разбирает строковое значение (из ENV или из БД) в типизированное."""
        if self.type is SettingType.INT:
            try:
                return int(raw)
            except ValueError as exc:
                raise ValidationError(f"{self.key}: ожидалось целое число, получено {raw!r}") from exc
        if self.type is SettingType.BOOL:
            low = raw.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True
            if low in ("0", "false", "no", "off"):
                return False
            raise ValidationError(f"{self.key}: ожидалось true/false, получено {raw!r}")
        if self.type is SettingType.STR_LIST:
            return tuple(p.strip() for p in raw.split(",") if p.strip())
        return raw.strip()

    def validate(self, value: Any) -> Any:
        """Проверяет типизированное значение. Возвращает его же (возможно, нормализованным)."""
        if self.type is SettingType.ENUM and self.choices and value not in self.choices:
            raise ValidationError(
                f"{self.key}: допустимы только {', '.join(self.choices)}, получено {value!r}"
            )
        if self.type is SettingType.PATH and value:
            # В контейнере пути всегда POSIX, но разработка идёт и на Windows,
            # поэтому абсолютным считаем путь в любой из двух нотаций.
            text = str(value)
            if not (PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute()):
                raise ValidationError(f"{self.key}: путь должен быть абсолютным, получено {value!r}")
        if self.check is not None:
            problem = self.check(value)
            if problem:
                raise ValidationError(f"{self.key}: {problem}")
        return value


# --------------------------------------------------------------------------- #
# Проверки
# --------------------------------------------------------------------------- #

# Зеркала Anime365. Проверено 2026-07-29: anime365.ru не отвечает, остальные живы.
# ВАЖНО: это серверный allowlist. base_url из UI сверяется с ним, поэтому подмена
# значения в браузере ничего не даёт. Новое зеркало добавляется только здесь или через ENV.
DEFAULT_MIRRORS: tuple[str, ...] = (
    "https://smotret-anime.app",
    "https://smotret-anime.org",
    "https://smotret-anime.online",
    "https://smotret-anime.net",
)

_HTTPS_HOST_RE = re.compile(r"^https://[a-z0-9.-]+$", re.IGNORECASE)


def _check_https_origin(value: str) -> str | None:
    if not value:
        return "не может быть пустым"
    if not _HTTPS_HOST_RE.match(value):
        return "ожидался https-origin без пути и порта, например https://smotret-anime.app"
    return None


def _check_mirror_list(value: tuple[str, ...]) -> str | None:
    if not value:
        return "список зеркал не может быть пустым"
    for item in value:
        problem = _check_https_origin(item)
        if problem:
            return f"{item!r}: {problem}"
    return None


def _non_negative(value: int) -> str | None:
    return None if value >= 0 else "не может быть отрицательным"


def _positive(value: int) -> str | None:
    return None if value > 0 else "должно быть больше нуля"


def _percent(value: int) -> str | None:
    return None if 0 <= value <= 100 else "должно быть в диапазоне 0..100"


def _check_umask(value: str) -> str | None:
    if not re.fullmatch(r"[0-7]{3,4}", value):
        return "ожидалась восьмеричная маска, например 022"
    return None


# --------------------------------------------------------------------------- #
# Реестр
# --------------------------------------------------------------------------- #

SETTINGS: tuple[Setting, ...] = (
    # --- API ---
    Setting(
        key="base_url",
        type=SettingType.ENUM,
        default=DEFAULT_MIRRORS[0],
        choices=DEFAULT_MIRRORS,
        group=SettingGroup.API,
        label_ru="Базовый адрес зеркала",
        help_ru=(
            "Выбирается из списка известных зеркал. embed всегда возвращает ссылки на "
            "smotret-anime.org, поэтому хост ссылок скачивания переписывается на выбранный."
        ),
    ),
    Setting(
        key="mirrors",
        type=SettingType.STR_LIST,
        default=DEFAULT_MIRRORS,
        group=SettingGroup.API,
        label_ru="Список зеркал (allowlist)",
        help_ru="Через запятую. Задаётся только на сервере: из UI не меняется — это защита от SSRF.",
        server_only=True,
        check=_check_mirror_list,
    ),
    Setting(
        key="access_token",
        type=SettingType.STR,
        default="",
        group=SettingGroup.API,
        label_ru="Access token",
        help_ru="Токен аккаунта с премиум-подпиской. Нужен для скачивания видео. Бессрочный.",
        secret=True,
    ),
    Setting(
        key="user_agent",
        type=SettingType.STR,
        default="Anime365Downloader/1.0",
        group=SettingGroup.API,
        label_ru="User-Agent",
        help_ru="Документация API просит указывать название программы.",
    ),
    Setting(
        key="request_rate_limit",
        type=SettingType.INT,
        default=5,
        group=SettingGroup.API,
        label_ru="Запросов к API в секунду",
        help_ru="Явного лимита у API не замечено, но ограничитель страхует от бана.",
        check=_positive,
    ),
    # --- Пути ---
    Setting(
        key="series_root",
        type=SettingType.PATH,
        default="/media/anime/library",
        group=SettingGroup.PATHS,
        label_ru="Корень сериалов",
        help_ru="Внутри контейнера. Все целевые пути проверяются как потомки этого корня.",
    ),
    Setting(
        key="movies_root",
        type=SettingType.PATH,
        default="/media/anime_movies/library",
        group=SettingGroup.PATHS,
        label_ru="Корень фильмов",
        help_ru=(
            "Куда складываются фильмы. Отдельно от сериалов: Jellyfin держит для них разные библиотеки."
        ),
    ),
    Setting(
        key="audio_root",
        type=SettingType.PATH,
        default="/media/anime_audio",
        group=SettingGroup.PATHS,
        label_ru="Корень фонотеки",
        help_ru=(
            "Отдельный корень для извлечённых звуковых дорожек. В библиотеку они не "
            "кладутся: Jellyfin индексировал бы их как видео без картинки."
        ),
    ),
    Setting(
        key="download_tmp",
        type=SettingType.PATH,
        default="/media/.a365-tmp",
        group=SettingGroup.PATHS,
        label_ru="Временная папка загрузок",
        help_ru=(
            "Желательно внутри того же монтирования, что библиотека: тогда готовый "
            "файл не копируется, а переименовывается — мгновенно и независимо от "
            "размера. Решает именно монтирование, а не диск и даже не файловая "
            "система: два отдельных тома docker с одной хостовой папки ядро считает "
            "разными монтированиями, и перемещение между ними идёт копированием. "
            "Практически это значит, что папку загрузок лучше держать подпапкой "
            "внутри уже примонтированной медиатеки, а не отдельным томом."
        ),
    ),
    # --- Загрузки ---
    Setting(
        key="max_concurrent_downloads",
        type=SettingType.INT,
        default=1,
        group=SettingGroup.DOWNLOADS,
        label_ru="Одновременных загрузок",
        help_ru="0 — без ограничения.",
        check=_non_negative,
    ),
    Setting(
        key="preferred_height",
        type=SettingType.INT,
        default=1080,
        group=SettingGroup.DOWNLOADS,
        label_ru="Предпочитаемое качество",
        help_ru=(
            "1080 — фактически максимум: даже у 4K-источников ступени скачивания "
            "ограничены 1080. Если ступени нет, берётся максимальная доступная ниже."
        ),
        check=_positive,
    ),
    Setting(
        key="max_attempts",
        type=SettingType.INT,
        default=5,
        group=SettingGroup.DOWNLOADS,
        label_ru="Попыток на задачу",
        help_ru=(
            "Сколько раз повторять неудачную задачу, прежде чем пометить её ошибкой."
        ),
        check=_positive,
    ),
    Setting(
        key="retry_backoff_base",
        type=SettingType.INT,
        default=30,
        group=SettingGroup.DOWNLOADS,
        label_ru="База экспоненциального бэкоффа, секунд",
        help_ru=(
            "Пауза между попытками растёт экспоненциально от этого значения."
        ),
        check=_positive,
    ),
    Setting(
        key="min_free_space_gb",
        type=SettingType.INT,
        default=10,
        group=SettingGroup.DOWNLOADS,
        label_ru="Минимум свободного места, ГБ",
        help_ru=(
            "Задача не стартует, если на любом из двух томов останется меньше: "
            "проверяются и временная папка, и папка библиотеки — обычно это разные "
            "монтирования, и файл переезжает между ними после загрузки."
        ),
        check=_non_negative,
    ),
    Setting(
        key="speed_limit_kbps",
        type=SettingType.INT,
        default=0,
        group=SettingGroup.DOWNLOADS,
        label_ru="Ограничение скорости, КБ/с",
        help_ru="0 — без ограничения.",
        check=_non_negative,
    ),
    Setting(
        key="try_direct_cdn",
        type=SettingType.BOOL,
        default=False,
        group=SettingGroup.DOWNLOADS,
        label_ru="Пробовать прямой CDN-хост",
        help_ru=(
            "Экспериментально. Ссылки идут через nginx-прокси перед BunnyCDN; прямой хост "
            "на тестовом файле был быстрее в 5.6 раза, но на реальном видео это не "
            "подтверждено. При любой ошибке — автоматический откат на обычную ссылку."
        ),
    ),
    # --- Расписания ---
    Setting(
        key="catalog_refresh_hours",
        type=SettingType.INT,
        default=1,
        group=SettingGroup.SCHEDULE,
        label_ru="Инкрементальный синк каталога, часов",
        help_ru=(
            "Новые тайтлы по afterId плюс обновление онгоингов. Онгоингов несколько "
            "сотен, так что проход занимает секунды."
        ),
        check=_positive,
    ),
    Setting(
        key="catalog_full_refresh_hours",
        type=SettingType.INT,
        default=24,
        group=SettingGroup.SCHEDULE,
        label_ru="Полный рескан каталога, часов",
        help_ru=(
            "Перечитывает весь каталог. У тайтлов нет updatedDateTime, поэтому правки "
            "существующих записей ловятся только полным рескaном — инкремент видит "
            "лишь новые."
        ),
        check=_positive,
    ),
    Setting(
        key="library_scan_hours",
        type=SettingType.INT,
        default=24,
        group=SettingGroup.SCHEDULE,
        label_ru="Сверочный скан библиотеки, часов",
        help_ru=(
            "Инкрементально по mtime. При включённом слежении за файловой системой это "
            "только страховка (пропущенные события, сетевые ФС, простой контейнера), "
            "поэтому интервал большой. Без слежения — основной механизм, ставьте 4-6."
        ),
        check=_positive,
    ),
    Setting(
        key="watch_filesystem",
        type=SettingType.BOOL,
        default=True,
        group=SettingGroup.SCHEDULE,
        label_ru="Следить за файловой системой (inotify)",
        help_ru=(
            "Библиотека обновляется в UI сразу после появления файлов, а не по расписанию. "
            "Стоит недорого: следим за каталогами, а не файлами, это ~1 КБ памяти ядра на "
            "каталог. НЕ работает на сетевых ФС (NFS, SMB) — там события от других хостов "
            "не доставляются; в этом случае автоматически откатываемся на расписание."
        ),
    ),
    Setting(
        key="watch_debounce_seconds",
        type=SettingType.INT,
        default=10,
        group=SettingGroup.SCHEDULE,
        label_ru="Пауза перед реакцией на изменения, секунд",
        help_ru=(
            "Копирование одного файла порождает десятки событий. Ждём тишины по папке "
            "указанное время и только потом пересканируем её — иначе будем дёргаться на "
            "каждый чанк записи."
        ),
        check=_positive,
    ),
    # --- Маппинг ---
    Setting(
        key="fuzzy_auto_threshold",
        type=SettingType.INT,
        default=88,
        group=SettingGroup.MATCHING,
        label_ru="Порог автоматического маппинга",
        help_ru="Оценка похожести названий 0..100, выше которой связь создаётся без подтверждения.",
        check=_percent,
    ),
    Setting(
        key="fuzzy_suggest_threshold",
        type=SettingType.INT,
        default=75,
        group=SettingGroup.MATCHING,
        label_ru="Порог предложения",
        help_ru="Ниже автоматического: попадает в «предложения» и требует подтверждения в UI.",
        check=_percent,
    ),
    # --- Файлы ---
    Setting(
        key="puid",
        type=SettingType.INT,
        default=1000,
        group=SettingGroup.FILES,
        label_ru="UID владельца создаваемых файлов",
        help_ru=(
            "Должен совпадать с владельцем медиабиблиотеки, иначе Jellyfin не прочитает скачанное. Узнать: id -u"
        ),
        check=_non_negative,
    ),
    Setting(
        key="pgid",
        type=SettingType.INT,
        default=1000,
        group=SettingGroup.FILES,
        label_ru="GID владельца создаваемых файлов",
        help_ru=(
            "Группа владельца создаваемых файлов. Узнать: id -g"
        ),
        check=_non_negative,
    ),
    Setting(
        key="umask",
        type=SettingType.STR,
        default="022",
        group=SettingGroup.FILES,
        label_ru="umask",
        help_ru=(
            "Восьмеричная маска прав для создаваемых файлов и папок, например 022."
        ),
        check=_check_umask,
    ),
    # --- Безопасность ---
    Setting(
        key="admin_login",
        type=SettingType.STR,
        default="admin",
        group=SettingGroup.SECURITY,
        label_ru="Логин администратора",
        help_ru=(
            "Имя, под которым вы входите. Пользователь с этим именем заводится при "
            "старте и всегда остаётся администратором, чтобы снятая галочка не "
            "заперла вас снаружи. Пароль ему задаёт соседняя настройка."
        ),
    ),
    Setting(
        key="ui_password",
        type=SettingType.STR,
        default="",
        group=SettingGroup.SECURITY,
        label_ru="Пароль на вход в UI",
        help_ru=(
            "Пусто — вход открыт (UI покажет предупреждение). Выставлять сервис наружу "
            "без пароля нельзя: через UI доступен ваш премиум-токен и запись в файловую систему."
        ),
        secret=True,
    ),
    # --- Фича-тогглы ---
    Setting(
        key="enable_ffprobe",
        type=SettingType.BOOL,
        default=False,
        group=SettingGroup.FEATURES,
        label_ru="Определять языки дорожек через ffprobe",
        help_ru=(
            "Зачем: чтобы в подсказке к серии было видно, на каком языке звук у скачанного "
            "файла, и чтобы панель загрузки не предлагала то, что у вас уже есть. "
            "Обычно не нужно — Jellyfin пишет список дорожек в <streamdetails> внутри .nfo, "
            "и оттуда это берётся бесплатно. Включайте, если у файлов нет .nfo. "
            "Работает фоновой джобой и запоминает результат: проверенный файл повторно не "
            "открывается. Цена — открытие каждого файла (читается заголовок, 0.37 МБ из 37), "
            "поэтому на медленном диске лучше держать выключенным."
        ),
    ),
    Setting(
        key="enable_jellyfin",
        type=SettingType.BOOL,
        default=False,
        group=SettingGroup.FEATURES,
        label_ru="Интеграция с Jellyfin",
        help_ru=(
            "Что даёт: после раскладки файлов просит Jellyfin пересканировать библиотеку, "
            "показывает на странице тайтла кнопку «Смотреть в Jellyfin», позволяет собирать "
            "коллекции и сверять, какие наши файлы Jellyfin у себя не распознал. "
            "Тайтл ищется у него по пути к папке, поэтому настраивать сопоставление не нужно."
        ),
    ),
    Setting(
        key="jellyfin_url",
        type=SettingType.STR,
        default="",
        group=SettingGroup.FEATURES,
        label_ru="Адрес Jellyfin (внутренний)",
        # Только с сервера, как и allowlist зеркал, и по той же причине.
        # Приложение само ходит по этому адресу изнутри сети сервера. Дать
        # возможность подставить его из браузера — значит отдать наружу
        # инструмент опроса внутренних хостов: SSRF в чистом виде, где ответ
        # чужого сервиса ещё и вернётся в интерфейс.
        server_only=True,
        help_ru=(
            "Куда ходит само приложение. Задаётся только на сервере: A365_JELLYFIN_URL. "
            "В docker это имя контейнера, например http://jellyfin:8096 — не IP: "
            "адрес в docker-сети переживёт не каждый перезапуск."
        ),
    ),
    Setting(
        key="jellyfin_public_url",
        type=SettingType.STR,
        default="",
        group=SettingGroup.FEATURES,
        label_ru="Адрес Jellyfin (публичный)",
        # Тоже только с сервера, хотя приложение по нему не ходит. Риск другой:
        # этот адрес подставляется в кнопку, на которую жмут. Правка из браузера
        # означала бы возможность увести пользователей на чужой хост под видом
        # своего Jellyfin.
        server_only=True,
        help_ru=(
            "Куда ведут ссылки, по которым вы кликаете в браузере, например "
            "https://jellyfin.example.com. Задаётся только на сервере: "
            "A365_JELLYFIN_PUBLIC_URL. Оставьте пустым, если Jellyfin не за обратным "
            "прокси — тогда берётся внутренний адрес."
        ),
    ),
    Setting(
        key="jellyfin_api_key",
        type=SettingType.STR,
        default="",
        group=SettingGroup.FEATURES,
        label_ru="API-ключ Jellyfin",
        help_ru=(
            "Заводится в самом Jellyfin: Панель управления → Дополнительно → API-ключи. "
            "Без него доступен только адрес сервера, всё остальное закрыто."
        ),
        secret=True,
    ),
)

BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}


def get(key: str) -> Setting:
    try:
        return BY_KEY[key]
    except KeyError:
        raise KeyError(f"неизвестная настройка {key!r}") from None


# Переменные, которых НЕТ в реестре и не будет в интерфейсе.
#
# Реестр — про настройки, которые пользователь меняет из браузера, и у каждой из
# них там есть поле. Здесь другое: вопросы эксплуатации конкретного сервера.
# Темп обхода, расписание фоновой джобы и адрес прокси задаёт тот, кто держит
# сервер, а не тот, кто открыл сайт, — поэтому им место только в окружении.
#
# Перечислены здесь ради одного: `.env.example` генерируется, и без этого списка
# они исчезали бы из него при каждой перегенерации.
ENV_ONLY: list[tuple[str, str, str]] = [
    (
        "A365_TRANSLATION_SCAN_HOURS",
        "24",
        "Как часто фоновая джоба обходит тайтлы и запоминает, какие типы перевода "
        "есть на сайте. Нужно для фильтра «покажи тайтлы с японскими субтитрами»: "
        "серверного фильтра по типу перевода в API нет, узнать можно только "
        "запросом на каждый тайтл. 0 отключает джобу целиком.",
    ),
    (
        "A365_TRANSLATION_SCAN_RPM",
        "30",
        "Запросов в минуту у этой джобы. Вдвое медленнее наблюдавшегося безопасного "
        "темпа: фоновой задаче спешить некуда, а нарваться на лимит не хочется. "
        "Считается отдельно от request_rate_limit — тот про запросы, которых ждёт человек.",
    ),
    (
        "A365_TRANSLATION_SCAN_SCOPE",
        "library",
        "Кого обходить: library — только тайтлы, привязанные к папкам библиотеки "
        "(сотни, минуты); catalog — весь каталог (29 тысяч, первый проход — часы).",
    ),
    (
        "HTTPS_PROXY",
        "",
        "Прокси для всех исходящих запросов к Anime365 — каталог, embed, скачивание, "
        "проксирование видео и субтитров в плеер. Весь трафик к сайту идёт по https, "
        "поэтому обычно достаточно одной этой переменной. Работают и HTTP_PROXY, "
        "и NO_PROXY — стандартные, те же, что понимают curl и requests.",
    ),
    (
        "ALL_PROXY",
        "",
        "Прокси для всех схем сразу; сюда пишут SOCKS5. Предпочтительно socks5h:// — "
        "с разрешением имён на стороне прокси, иначе DNS-запросы уйдут мимо VPN и "
        "блокировка по имени останется в силе.",
    ),
]


#: Пути, которые обязаны быть непересекающимися.
PATH_KEYS = ("series_root", "movies_root", "audio_root", "download_tmp")

PATH_LABELS = {
    "series_root": "корень сериалов",
    "movies_root": "корень фильмов",
    "audio_root": "корень фонотеки",
    "download_tmp": "временная папка загрузок",
}

#: То же самое в родительном падеже — для оборота «лежит внутри …».
#: Отдельный словарь, а не склонение на лету: правил тут четыре штуки,
#: и подключать ради них морфологию несоразмерно.
PATH_LABELS_GENITIVE = {
    "series_root": "корня сериалов",
    "movies_root": "корня фильмов",
    "audio_root": "корня фонотеки",
    "download_tmp": "временной папки загрузок",
}


def check_path_layout(paths: dict[str, str]) -> list[str]:
    """Ищет вложенность и совпадения среди корней.

    Совпадающие или вложенные пути ломают вещи, которые чинятся только руками:

    * временная папка внутри библиотеки — сканер увидит `.part` и наполовину
      скачанные файлы и заведёт их как серии; когда придёт настоящий файл,
      рядом окажется дубль, а привязка будет указывать на мусор;
    * фонотека внутри библиотеки — ровно то, чего мы избегали, заводя ей
      отдельный корень: Jellyfin проиндексирует звук как видео без картинки;
    * сериалы внутри фильмов (или наоборот) — раскладка перестаёт быть
      однозначной, и один и тот же файл попадает в обе библиотеки.

    Поэтому запрещаем на входе, а не чиним последствия. Возвращаем список
    внятных сообщений — по одному на пару.
    """
    resolved: dict[str, PurePosixPath] = {}
    for key in PATH_KEYS:
        value = (paths.get(key) or "").strip()
        if value:
            # Хвостовой слеш и `.` в середине не должны выдавать одно и то же
            # за разное: `/media/anime` и `/media/anime/` — один путь.
            resolved[key] = PurePosixPath(value.rstrip("/") or "/")

    problems: list[str] = []
    keys = list(resolved)
    for i, first in enumerate(keys):
        for second in keys[i + 1 :]:
            a, b = resolved[first], resolved[second]
            if a == b:
                problems.append(
                    f"{PATH_LABELS[first]} и {PATH_LABELS[second]} указывают на один путь "
                    f"({a}) — задайте разные"
                )
            elif a.is_relative_to(b) or b.is_relative_to(a):
                inner, outer = (first, second) if a.is_relative_to(b) else (second, first)
                problems.append(
                    f"{PATH_LABELS[inner]} ({resolved[inner]}) лежит внутри "
                    f"{PATH_LABELS_GENITIVE[outer]} ({resolved[outer]}) — вложенность недопустима"
                )
    return problems


def render_env_example() -> str:
    """Генерирует .env.example из реестра, чтобы он не расходился с кодом."""
    lines = [
        "# Сгенерировано из backend/app/core/settings_registry.py — не правьте вручную.",
        "# Приоритет: значение по умолчанию < эта переменная < правка в UI.",
        "",
    ]
    for group in SettingGroup:
        group_settings = [s for s in SETTINGS if s.group is group]
        if not group_settings:
            continue
        lines.append(f"# --- {group.value} ---")
        for s in group_settings:
            if s.help_ru:
                for chunk in _wrap(s.help_ru):
                    lines.append(f"# {chunk}")
            shown = "" if s.secret else _format_default(s)
            lines.append(f"{s.env}={shown}")
            lines.append("")

    lines.append("# --- только через окружение, в интерфейсе этих полей нет ---")
    lines.append("")
    for name, default, help_text in ENV_ONLY:
        for chunk in _wrap(help_text):
            lines.append(f"# {chunk}")
        # Пустые значения оставляем закомментированными: раскомментированный
        # HTTPS_PROXY= сбросил бы прокси, унаследованный от хоста.
        lines.append(f"{name}={default}" if default else f"# {name}=")
        lines.append("")

    return "\n".join(lines)


def _format_default(s: Setting) -> str:
    if s.type is SettingType.STR_LIST:
        return ",".join(s.default)
    if s.type is SettingType.BOOL:
        return "true" if s.default else "false"
    return str(s.default)


def _wrap(text: str, width: int = 92) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out
