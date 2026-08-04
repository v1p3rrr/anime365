"""Проксирование видео и субтитров для встроенного плеера.

⚠️ Ключевое ограничение: **access_token не должен попасть в браузер.**

Ссылки скачивания содержат токен в query-параметре. Отдать такую ссылку в
`<video src=...>` означало бы показать бессрочный премиум-токен в исходниках
страницы, в истории браузера и в логах любого прокси по пути. Поэтому браузер
обращается сюда, а токен подставляется на сервере.

Второе: это **не** открытый прокси. Эндпоинт принимает идентификатор перевода,
а не URL. Ссылку он получает сам, через `embed`, и дополнительно сверяет хост
с allowlist — подменённый ответ API не заставит проксировать что попало.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.core import errors
from app.core.errors import ApiError
from fastapi.responses import RedirectResponse, StreamingResponse

from app.api.deps import rate_limit
from app.core.config import settings
from app.core.permissions import Permission, require
from app.services.anime365 import Anime365Client, Anime365Error, SubscriptionRequired
from app.services.downloader import is_allowed_target, pick_download_url
from app.services.title_service import get_embed

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/stream",
    tags=["stream"],
    # Весь роутер — проксирование видео и субтитров через наш
    # премиум-токен, поэтому право проверяется на входе целиком.
    dependencies=[Depends(require(Permission.WATCH))],
)

# Заголовки, которые имеет смысл передать наверх от плеера к CDN.
# Range обязателен: без него перемотка не работает, а браузер тянет весь файл.
FORWARDED_REQUEST_HEADERS = ("range",)

# Что отдаём обратно плееру. Остальное (куки, заголовки CDN) не нужно и может
# нести лишние сведения о нашем окружении.
FORWARDED_RESPONSE_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
)

CHUNK_SIZE = 1 << 18  # 256 КБ — достаточно для плавной отдачи, не раздувает память


def attachment_headers(filename: str) -> dict[str, str]:
    """Заголовок «сохрани это файлом с таким именем».

    Имя кодируется дважды: голое `filename=` для старых клиентов и
    `filename*=UTF-8''…` по RFC 5987 — без второго кириллица и японские
    названия превращаются в мусор. В голом варианте не-ASCII вырезается, а не
    транслитерируется: гадать не наше дело, а RFC-вариант поймёт любой
    современный браузер.
    """
    ascii_name = "".join(ch if 32 < ord(ch) < 127 and ch != '"' else "_" for ch in filename)
    quoted = quote(filename, safe="")
    return {"Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'}


def _pick_stream_url(embed_stream: list[dict[str, Any]], height: int | None) -> str | None:
    """Ссылка на поток нужного качества.

    Берём максимальную ступень не выше запрошенной — то же правило, что и при
    скачивании: если ровно такой нет, лучше отдать что есть ниже, чем отказать.
    """
    by_height: dict[int, str] = {}
    for item in embed_stream or []:
        urls = item.get("urls") or []
        item_height = item.get("height")
        if urls and item_height:
            by_height[int(item_height)] = urls[0]

    if not by_height:
        return None
    if height is None:
        return by_height[max(by_height)]

    suitable = [h for h in by_height if h <= height]
    return by_height[max(suitable) if suitable else min(by_height)]


@router.get(
    "/{translation_id}/video",
    # Плеер открывает поток один раз и потом тянет диапазоны по тому же URL,
    # но каждый новый запрос — это обращение к embed. Ограничиваем частоту.
    dependencies=[
        # Просмотр — отдельное право: роль «пусть смотрит, но ничего не качает»
        # без него не имела бы смысла, а видео шло бы любому вошедшему.
        Depends(require(Permission.WATCH)),
        Depends(rate_limit("stream", limit=60, window=60)),
    ],
)
async def stream_video(
    translation_id: int,
    height: Annotated[int | None, Query(description="Желаемая высота, максимум из доступных ниже")] = None,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> StreamingResponse:
    """Отдаёт видео перевода, подставляя токен на сервере."""
    try:
        async with Anime365Client() as client:
            info = await client.embed(translation_id)
    except SubscriptionRequired as exc:
        raise ApiError(403, errors.SUBSCRIPTION_REQUIRED, str(exc), reason=str(exc)) from exc
    except Anime365Error as exc:
        raise errors.upstream_unavailable(exc) from exc

    url = _pick_stream_url(info.stream, height)
    if not url:
        # У части переводов потоковых ссылок нет — тогда играем то же, что качаем.
        for item in info.download:
            if item.get("url"):
                url = item["url"]
                break

    if not url:
        raise ApiError(404, errors.NO_PLAYABLE_LINKS, "У этого перевода нет ссылок для воспроизведения")

    if not is_allowed_target(url):
        # Ссылку выдал API, но доверять ей вслепую нельзя: подменённый ответ
        # превратил бы эндпоинт в открытый прокси на произвольный хост.
        log.error("Отклонён посторонний хост при воспроизведении: %s", url)
        raise ApiError(502, errors.UNKNOWN_HOST, "Ссылка ведёт на неизвестный хост")

    headers = {"User-Agent": settings.get_str("user_agent")}
    if range_header:
        headers["Range"] = range_header

    client = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=None))
    try:
        request = client.build_request("GET", url, headers=headers)
        upstream = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        detail = str(exc) or type(exc).__name__
        raise ApiError(502, errors.STREAM_OPEN_FAILED, f"Не удалось открыть поток: {detail}", reason=detail) from exc

    if upstream.status_code >= 400:
        status = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        raise ApiError(502, errors.CDN_ERROR, f"CDN ответил {status}", status=status)

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(CHUNK_SIZE):
                yield chunk
        finally:
            # Соединения закрываем всегда: брошенная вкладка не должна
            # оставлять висящий сокет к CDN.
            await upstream.aclose()
            await client.aclose()

    passthrough = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() in FORWARDED_RESPONSE_HEADERS
    }
    return StreamingResponse(body(), status_code=upstream.status_code, headers=passthrough)


@router.get(
    "/{translation_id}/download",
    dependencies=[
        Depends(require(Permission.WATCH)),
        Depends(rate_limit("download-link", limit=30, window=60)),
    ],
)
async def download_video(
    translation_id: int,
    height: Annotated[int | None, Query(description="Желаемая высота, максимум из доступных ниже")] = None,
) -> RedirectResponse:
    """Отправляет браузер качать видео **прямо у CDN**, минуя наш сервер.

    Зачем не через себя. Серия в 1080p весит около 230 МБ; проксировать её ради
    того, чтобы человек положил файл себе на диск, — это гонять те же байты
    дважды через домашний сервер, у которого канал один на всё.

    ⚠️ Почему это можно, хотя токен в браузер отдавать нельзя ([D-39](../../../claude/09-decisions.md)).
    Проверено на живом API: ссылка `translations/mp4/{id}` с токеном отвечает
    **302**, и в `Location` стоит подписанный адрес CDN
    (`…b-cdn.net/route/?m=…&r=…`), где токена нет вовсе. Мы забираем этот адрес
    сами — не выкачивая тело, только заголовок редиректа, — и отдаём браузеру.
    Токен остаётся на сервере, а человек получает файл напрямую.

    Заодно CDN сам присылает `content-disposition` с именем, которым Anime365
    называет свои файлы (`[Anime365] Sousou no Frieren - 06 (t4505262).mp4`),
    и `accept-ranges` — то есть докачка обрыва работает средствами браузера.

    Подпись у ссылки короткоживущая, поэтому эндпоинт возвращает именно
    редирект, а не сохранённый заранее адрес: браузер идёт по нему сразу.
    """
    try:
        async with Anime365Client() as client:
            info = await client.embed(translation_id)
    except SubscriptionRequired as exc:
        raise ApiError(403, errors.SUBSCRIPTION_REQUIRED, str(exc), reason=str(exc)) from exc
    except Anime365Error as exc:
        raise errors.upstream_unavailable(exc) from exc

    url, _resolved = pick_download_url({"download": info.download}, height or 1080)
    if not url or not is_allowed_target(url):
        raise ApiError(404, errors.FILE_NOT_FOUND, "Ссылка на скачивание недоступна")

    async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as http:
        try:
            response = await http.get(url, headers={"User-Agent": settings.get_str("user_agent")})
        except httpx.HTTPError as exc:
            detail = str(exc) or type(exc).__name__
            raise ApiError(
                502, errors.UPSTREAM_UNAVAILABLE, f"Не удалось получить ссылку: {detail}", reason=detail
            ) from exc

    location = response.headers.get("location")
    if response.status_code not in (301, 302, 303, 307, 308) or not location:
        # Без редиректа отдавать нечего: сама ссылка несёт токен.
        raise ApiError(
            502,
            errors.UPSTREAM_UNAVAILABLE,
            f"Anime365 не выдал ссылку на файл (ответ {response.status_code})",
            reason=str(response.status_code),
        )

    if not is_allowed_target(location):
        # Подменённый ответ не должен уводить человека на посторонний хост.
        raise ApiError(502, errors.UPSTREAM_UNAVAILABLE, "Anime365 увёл ссылку на неизвестный хост")

    token = settings.get_str("access_token")
    if token and token in location:
        # Страховка на случай, если сайт однажды начнёт прокидывать токен
        # дальше: лучше отказать, чем показать его браузеру.
        log.error("В ссылке CDN оказался токен — редирект не отдан")
        raise ApiError(502, errors.UPSTREAM_UNAVAILABLE, "Ссылка на файл пришла в небезопасном виде")

    return RedirectResponse(location, status_code=302)


# Второй адрес с расширением — не косметика. Плагин субтитров плеера
# определяет формат по РАСШИРЕНИЮ адреса (`getExt(url) === "ass"`), и на пути
# без него молча ничего не грузит. Оставляем оба: старый уже мог где-то
# сохраниться, а ломать работающие ссылки ради нового ни к чему.
@router.get(
    "/{translation_id}/subtitles.ass",
    dependencies=[Depends(require(Permission.WATCH))],
)
@router.get(
    "/{translation_id}/subtitles",
    dependencies=[Depends(require(Permission.WATCH))],
)
async def stream_subtitles(
    translation_id: int,
    download: Annotated[bool, Query(description="Отдать файлом, а не текстом для плеера")] = False,
) -> Response:
    """Файл субтитров перевода.

    Токен для субтитров не нужен, но проксируем их всё равно: иначе браузер
    пошёл бы на сторонний домен, что запрещено нашей же CSP (`connect-src 'self'`).

    ⚠️ Здесь, в отличие от видео, редирект на Anime365 не годится, хотя ссылка и
    открыта. Причин две: `content-disposition` у неё нет, поэтому браузер показал
    бы текст субтитров вместо сохранения файла, а атрибут `download` у ссылки на
    чужой домен игнорируется. Цена проксирования при этом копеечная — файл весит
    десятки килобайт, а не сотни мегабайт, как видео.
    """
    try:
        embed = await get_embed(translation_id)
    except SubscriptionRequired as exc:
        raise ApiError(403, errors.SUBSCRIPTION_REQUIRED, str(exc), reason=str(exc)) from exc
    except Anime365Error as exc:
        raise errors.upstream_unavailable(exc) from exc

    if not embed.get("has_subtitles"):
        raise ApiError(404, errors.SUBTITLES_HARDSUB, "У этого перевода субтитры вшиты в видео, отдельного файла нет")

    async with Anime365Client() as client:
        info = await client.embed(translation_id)

    url = info.subtitles_url
    if not url or not is_allowed_target(url):
        raise ApiError(404, errors.SUBTITLES_UNAVAILABLE, "Ссылка на субтитры недоступна")

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client_http:
        try:
            response = await client_http.get(
                url, headers={"User-Agent": settings.get_str("user_agent")}
            )
        except httpx.HTTPError as exc:
            detail = str(exc) or type(exc).__name__
            raise ApiError(502, errors.SUBTITLES_FETCH_FAILED, f"Не удалось получить субтитры: {detail}", reason=detail) from exc

    if response.status_code >= 400:
        raise ApiError(502, errors.SUBTITLES_SERVER_ERROR, f"Сервер субтитров ответил {response.status_code}", status=response.status_code)

    headers = {"Cache-Control": "private, max-age=3600"}
    if download:
        headers |= attachment_headers(anime365_style_name(translation_id, ".ass"))

    # ASS отдаём как текст: разбирать его будет плеер на стороне браузера.
    return Response(
        response.content,
        media_type="text/plain; charset=utf-8",
        headers=headers,
    )


def anime365_style_name(translation_id: int, suffix: str) -> str:
    """Имя файла в том же виде, в каком его называет сам Anime365.

    `[Anime365] Sousou no Frieren - 06 (t4505262).ass` — ровно так подписан
    видеофайл, который CDN отдаёт по соседней кнопке. Совпадение не случайное:
    человек качает видео и субтитры к нему, и приятно, когда рядом в папке
    «Загрузки» они лежат с одинаковым именем и разными расширениями.

    Правилами именования библиотеки (`services/naming.py`) здесь не пользуемся
    намеренно: они существуют, чтобы Jellyfin связал файл с соседями в своей
    папке, а тут файл едет на чужой компьютер, где никаких соседей нет.
    """
    from app.core.db import session_scope
    from app.models.catalog import Episode, Series, Translation
    from app.services.naming import sanitize_component

    with session_scope() as session:
        translation = session.get(Translation, translation_id)
        if translation is None:
            return f"anime365_{translation_id}{suffix}"
        series = session.get(Series, translation.series_id)
        episode = session.get(Episode, translation.episode_id) if translation.episode_id else None

        titles = (series.titles if series else None) or {}
        name = titles.get("romaji") or titles.get("en") or titles.get("ru") or f"series-{translation.series_id}"
        number = (episode.episode_int if episode else None) or ""

    # ⚠️ Санитизируем только название, а скобки дописываем после. Общая
    # `sanitize_component` вырезает `[` и `]` — и правильно делает: в библиотеке
    # по ним разбирается релиз-группа при следующем скане. Но здесь файл едет на
    # чужой компьютер, никакой библиотеки рядом нет, а совпадение с именем
    # видеофайла от самого Anime365 дороже.
    stem = f"[Anime365] {sanitize_component(name)}"
    if number:
        # Двузначный номер — как у самого сайта: «- 06», а не «- 6».
        try:
            stem += f" - {int(float(number)):02d}"
        except (TypeError, ValueError):
            stem += f" - {sanitize_component(str(number))}"
    return f"{stem} (t{translation_id}){suffix}"


# --------------------------------------------------------------------------- #
# Фонотека
# --------------------------------------------------------------------------- #

# Сколько отдаём за раз. Читать файл целиком в память нельзя: дорожка серии
# весит десятки мегабайт, а слушают их с телефона по одной.
AUDIO_CHUNK = 256 * 1024




def serve_file_with_range(
    path: Path,
    media_type: str,
    range_header: str | None,
    filename: str | None = None,
) -> Response:
    """Отдаёт файл с диска, поддерживая перемотку.

    Range обязателен для медиа: без него браузер не даёт перемотку вовсе — он
    не станет запрашивать середину файла, если сервер не обещал, что умеет её
    отдать, и ползунок останется декоративным.

    ⚠️ `path` обязан приходить из базы по идентификатору, а не из параметра
    запроса: принять путь от клиента здесь означало бы отдать наружу чтение
    любого файла на сервере.
    """
    size = path.stat().st_size
    start, end = 0, size - 1
    status_code = 200

    if range_header and range_header.startswith("bytes="):
        raw = range_header.removeprefix("bytes=").split(",")[0]
        first, _, last = raw.partition("-")
        try:
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            elif last:
                # Форма «bytes=-500»: последние N байт.
                start = max(0, size - int(last))
        except ValueError:
            # Кривой заголовок не повод отказывать: отдаём файл целиком.
            start, end = 0, size - 1
        else:
            end = min(end, size - 1)
            if start > end:
                # Запрошено за пределами файла — по RFC это 416, и браузер
                # умеет на него реагировать, в отличие от молчаливого 200.
                raise HTTPException(
                    status_code=416, headers={"Content-Range": f"bytes */{size}"}
                )
            status_code = 206

    def body():
        with path.open("rb") as handle:
            handle.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = handle.read(min(AUDIO_CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if filename:
        # Range остаётся и при сохранении: браузер докачает оборванное сам.
        headers |= attachment_headers(filename)

    return StreamingResponse(
        body(), status_code=status_code, media_type=media_type, headers=headers
    )


@router.get(
    "/audio/{task_id}",
    dependencies=[Depends(require(Permission.WATCH))],
)
def stream_audio(
    task_id: int,
    download: Annotated[bool, Query(description="Сохранить файлом, а не играть в браузере")] = False,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> Response:
    """Отдаёт извлечённую звуковую дорожку.

    ⚠️ Путь берётся из записи задачи по её идентификатору, а НЕ из параметра
    запроса: принять путь от клиента здесь означало бы отдать наружу чтение
    любого файла на сервере.

    Range обязателен. Без него `<audio>` не даёт перемотку вовсе: браузер не
    станет запрашивать середину файла, если сервер не обещал, что умеет её
    отдать, — и ползунок останется декоративным.
    """
    from app.services.audio_library import content_type_for, track_path

    path = track_path(task_id)
    if path is None:
        raise ApiError(404, errors.FILE_NOT_FOUND, "Дорожка не найдена или файл удалён")

    return serve_file_with_range(
        path, content_type_for(path), range_header, path.name if download else None
    )


# Расширение → тип содержимого для локальных видеофайлов. Список короткий
# намеренно: это то, что вообще может лежать в аниме-библиотеке.
LOCAL_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".m4v": "video/mp4",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
}


@router.get(
    "/local/{file_id}",
    dependencies=[Depends(require(Permission.WATCH))],
)
def stream_local(
    file_id: int,
    download: Annotated[bool, Query(description="Сохранить файлом, а не играть в браузере")] = False,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> Response:
    """Отдаёт уже скачанный файл библиотеки.

    ⚠️ Путь берётся из записи по идентификатору, а не из параметра запроса:
    иначе это был бы способ прочитать любой файл на сервере.

    Про форматы. Браузер играет далеко не всё, что лежит в библиотеке: `.mkv`
    с H.264 и AAC обычно проигрывается, а с HEVC или FLAC внутри — уже нет, и
    это зависит от браузера, а не от нас. Мы отдаём файл как есть и не
    перекодируем: перекодирование на слабом домашнем сервере съело бы процессор
    ради одной вкладки. Что именно не сыграло, показывает сам плеер.
    """
    from app.core.db import session_scope
    from app.models.library import LibraryFile, LibraryFolder, LibraryRoot

    with session_scope() as session:
        row = session.get(LibraryFile, file_id)
        if row is None or row.kind != "video":
            raise ApiError(404, errors.FILE_NOT_FOUND, "Файл не найден")
        folder = session.get(LibraryFolder, row.folder_id)
        root = session.get(LibraryRoot, folder.root_id) if folder else None
        if folder is None or root is None:
            raise ApiError(404, errors.FILE_NOT_FOUND, "Файл не найден")
        path = Path(root.path) / folder.rel_path / row.filename

    if not path.exists():
        # Файл могли удалить после скана — это не ошибка сервера.
        raise ApiError(404, errors.FILE_NOT_FOUND, "Файл удалён с диска")

    media_type = LOCAL_VIDEO_TYPES.get(path.suffix.lower(), "application/octet-stream")
    # Имя берём то, что лежит на диске: оно построено по правилам библиотеки и
    # человеку понятнее любого выдуманного здесь заново.
    return serve_file_with_range(path, media_type, range_header, path.name if download else None)


# --------------------------------------------------------------------------- #
# Субтитры к локальному видео
# --------------------------------------------------------------------------- #


@router.get(
    "/local/{file_id}/subtitle-tracks",
    dependencies=[Depends(require(Permission.WATCH))],
)
async def local_subtitle_tracks(file_id: int) -> dict[str, Any]:
    """Что можно показать поверх этого файла: соседние файлы и дорожки внутри.

    Спрашивается при открытии плеера, а не при построении страницы тайтла: у
    тайтла бывает три сотни серий, и обходить ffprobe'ом их все ради списка,
    который понадобится для одной, — это часы работы диска впустую.
    """
    from app.services.local_subtitles import tracks_for

    return await tracks_for(file_id)


@router.get(
    # ⚠️ Точка между параметрами обязательна. Без разделителя
    # (`{file_id}{ext}`) жадный `[^/]+` съедает почти всё: из «12.srt»
    # получается file_id=«12.sr» и ext=«t», и запрос падает на разборе числа.
    # Тот же приём уже стоит у логотипов студий.
    "/local/subtitle/{file_id}.{ext}",
    dependencies=[Depends(require(Permission.WATCH))],
)
def local_subtitle_file(
    file_id: int,
    ext: str,
    download: Annotated[bool, Query(description="Сохранить файлом, а не отдать плееру")] = False,
) -> Response:
    """Отдаёт соседний файл субтитров из библиотеки.

    ⚠️ Расширение в адресе — не украшение. Плеер определяет формат субтитров по
    расширению (`getExt(url) === 'ass'`), и на пути без него молча ничего не
    грузит. Само значение `ext` при этом ни на что не влияет: файл берётся из
    базы по идентификатору, а путь собирается на сервере.
    """
    from app.services.local_subtitles import subtitle_file_path

    path = subtitle_file_path(file_id)
    if path is None:
        raise ApiError(404, errors.FILE_NOT_FOUND, "Файл субтитров не найден")

    headers = {"Cache-Control": "private, max-age=3600"}
    if download:
        headers |= attachment_headers(path.name)

    # Субтитры — текст, и разбирает их плеер в браузере. Отдаём как есть, не
    # пытаясь угадать кодировку: перекодировать вслепую хуже, чем отдать байты.
    return Response(
        path.read_bytes(),
        media_type="text/plain; charset=utf-8",
        headers=headers,
    )


@router.get(
    "/local/{file_id}/embedded/{index}.ass",
    dependencies=[Depends(require(Permission.WATCH))],
)
async def local_embedded_subtitle(
    file_id: int,
    index: int,
    download: Annotated[bool, Query(description="Сохранить файлом, а не отдать плееру")] = False,
) -> Response:
    """Вытаскивает дорожку субтитров из контейнера.

    Дорого: ffmpeg проходит файл целиком, собирая разбросанные по нему пакеты.
    Поэтому результат кэшируется на диске, а запрос делается только когда
    человек выбрал именно эту дорожку.

    Сохранить её себе тоже можно, и это тот же самый вырезанный файл: второй
    раз ffmpeg не запускается — ответ уже в кэше после того, как дорожку
    показали в плеере.
    """
    from app.services.audio import AudioError
    from app.services.local_subtitles import embedded_subtitle

    try:
        data = await embedded_subtitle(file_id, index)
    except AudioError as exc:
        raise ApiError(
            404, errors.SUBTITLES_UNAVAILABLE, str(exc), reason=str(exc)
        ) from exc

    headers = {"Cache-Control": "private, max-age=3600"}
    if download:
        headers |= attachment_headers(f"subtitles-{file_id}-{index}.ass")

    return Response(
        data,
        media_type="text/plain; charset=utf-8",
        headers=headers,
    )
