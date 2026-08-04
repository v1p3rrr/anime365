"""Субтитры к уже скачанному видео: соседние файлы и дорожки внутри контейнера.

Задача разбита надвое, потому что источника два и стоят они по-разному.

**Соседний файл** (`… .ru.srt` рядом с `… .mkv`) — бесплатный источник: он уже
разобран сканером, лежит в базе, и отдать его значит прочитать десяток
килобайт с диска. Так размечает библиотеку Jellyfin, и у пользователя субтитры
лежат именно так.

**Дорожка внутри контейнера** — дорогой: чтобы её вытащить, ffmpeg обязан
пройти файл и собрать разбросанные по нему пакеты. На полутора гигабайтах с
диска 5400 rpm это ощутимо, поэтому:

* индексы дорожек спрашиваем у ffprobe (в `.nfo` их нет — там только языки, а
  для `-map` нужен номер), и только когда человек открыл плеер на этом файле;
* результат извлечения кладём в кэш рядом с временной папкой загрузок и при
  повторном открытии отдаём с диска;
* ключ кэша включает `mtime` файла: файл могли заменить на месте под тем же
  именем, и старые субтитры к новому видео уже не подходят.

Формат на выходе всегда ASS. Не из любви к нему: плеер определяет формат по
расширению адреса, а libass рисует ASS с оформлением и позиционированием — то
есть ровно то, ради чего в аниме субтитры и существуют. SRT ffmpeg превращает
в ASS сам, теряя при этом нечего: в SRT оформления нет.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.db import session_scope
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot
from app.services.audio import AudioError, ffmpeg_available
from app.services.languages import normalize_lang
from sqlalchemy import select

log = logging.getLogger(__name__)

#: Расширения, которые считаем субтитрами. Совпадает со списком сканера —
#: иначе в списке дорожек оказалось бы то, чего библиотека не видит.
SUBTITLE_SUFFIXES = {".ass", ".ssa", ".srt", ".vtt", ".sub"}

#: Сколько ждём ffmpeg на одном файле. Извлечение требует прохода по контейнеру,
#: и на большом файле с медленного диска это десятки секунд; но повиснуть
#: навсегда процесс тоже не должен — вкладку к тому времени давно закроют.
EXTRACT_TIMEOUT = 180.0


@dataclass(frozen=True)
class SubtitleTrack:
    """Одна доступная дорожка субтитров к локальному видео."""

    #: Устойчивый ключ для клиента: `file:12` или `embedded:3`.
    id: str
    #: `file` — соседний файл, `embedded` — дорожка внутри контейнера.
    source: str
    #: Двухбуквенный код языка, если удалось определить.
    lang: str | None
    #: Имя файла или название дорожки — то, по чему человек её узнает.
    label: str
    #: Адрес, с которого плеер заберёт субтитры.
    url: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source": self.source,
            "lang": self.lang,
            "label": self.label,
            "url": self.url,
        }


def _resolve(file_id: int) -> tuple[LibraryFile, Path] | None:
    """Запись файла и путь к нему. `None` — записи нет или файл удалён.

    ⚠️ Путь собирается из корня и относительного пути в базе, а не приходит от
    клиента: принять путь параметром означало бы отдать чтение любого файла на
    сервере.
    """
    with session_scope() as session:
        row = session.get(LibraryFile, file_id)
        if row is None:
            return None
        folder = session.get(LibraryFolder, row.folder_id)
        root = session.get(LibraryRoot, folder.root_id) if folder else None
        if folder is None or root is None:
            return None
        session.expunge(row)
        return row, Path(root.path) / folder.rel_path / row.filename


def _sidecar_files(video: LibraryFile) -> list[LibraryFile]:
    """Файлы субтитров, относящиеся к этому видео.

    Правило то же, по которому их связывает Jellyfin: имя субтитров начинается
    с имени видеофайла без расширения. Именно поэтому при докачке субтитров мы
    берём basename соседа как есть — иначе связь рвётся.

    Если по этому правилу не нашлось ничего, отдаём все субтитры той же папки,
    привязанные к той же серии: библиотеку могли собрать не по соглашению
    Jellyfin, и в этом случае «ничего» — худший ответ из возможных.
    """
    stem = Path(video.filename).stem
    with session_scope() as session:
        rows = (
            session.execute(
                select(LibraryFile).where(
                    LibraryFile.folder_id == video.folder_id,
                    LibraryFile.kind == "subtitle",
                )
            )
            .scalars()
            .all()
        )

        matching = [row for row in rows if row.filename.startswith(stem)]
        if not matching and video.episode is not None:
            matching = [row for row in rows if row.episode == video.episode]

        for row in matching:
            session.expunge(row)
        return matching


async def _embedded_streams(path: Path) -> list[dict[str, object]]:
    """Дорожки субтитров внутри контейнера, с индексами.

    Индекс обязателен: `-map 0:{index}` берёт конкретную дорожку, а не «первую
    субтитровую», и при нескольких это разные вещи. В `.nfo` индексов нет
    вовсе — там только языки, поэтому здесь без ffprobe не обойтись.
    """
    if not ffmpeg_available():
        return []

    from app.services.audio import _run

    code, stdout, stderr = await _run(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index,codec_name:stream_tags=language,title",
        "-of",
        "json",
        str(path),
    )
    if code != 0:
        log.warning("ffprobe не прочитал дорожки субтитров %s: %s", path.name, stderr[:200])
        return []

    try:
        payload = json.loads(stdout or b"{}")
    except json.JSONDecodeError:
        return []

    return payload.get("streams") or []


async def tracks_for(file_id: int) -> dict[str, object]:
    """Что можно показать поверх этого видео.

    Соседние файлы идут первыми: они бесплатны и в библиотеке пользователя
    встречаются чаще. Дорожки контейнера — следом, с пометкой источника, потому
    что первая появляется мгновенно, а вторая ждёт ffmpeg.
    """
    resolved = _resolve(file_id)
    if resolved is None:
        return {"tracks": [], "ffmpeg": ffmpeg_available()}

    video, path = resolved
    tracks: list[SubtitleTrack] = []

    for row in _sidecar_files(video):
        suffix = Path(row.filename).suffix.lower()
        if suffix not in SUBTITLE_SUFFIXES:
            continue
        tracks.append(
            SubtitleTrack(
                id=f"file:{row.id}",
                source="file",
                lang=row.lang,
                label=row.filename,
                # Расширение в адресе обязательно: плеер определяет формат
                # именно по нему, и на пути без расширения молча ничего не
                # грузит. Отдаём под родным расширением файла.
                url=f"/api/stream/local/subtitle/{row.id}{suffix}",
            )
        )

    if path.exists():
        for item in await _embedded_streams(path):
            tags = item.get("tags") or {}
            index = item.get("index")
            if index is None:
                continue
            lang = normalize_lang(tags.get("language"))
            title = (tags.get("title") or "").strip()
            tracks.append(
                SubtitleTrack(
                    id=f"embedded:{index}",
                    source="embedded",
                    lang=lang,
                    label=title or f"{item.get('codec_name') or '?'} #{index}",
                    url=f"/api/stream/local/{file_id}/embedded/{index}.ass",
                )
            )

    return {"tracks": [track.as_dict() for track in tracks], "ffmpeg": ffmpeg_available()}


def subtitle_file_path(file_id: int) -> Path | None:
    """Путь к соседнему файлу субтитров — для отдачи браузеру."""
    resolved = _resolve(file_id)
    if resolved is None:
        return None
    row, path = resolved
    if row.kind != "subtitle" or not path.exists():
        return None
    return path


def _cache_dir() -> Path:
    """Куда складывать извлечённые дорожки.

    Рядом с временной папкой загрузок, а не в корне библиотеки: библиотека
    сканируется, и появившиеся там `.ass` уехали бы в неё как настоящие
    субтитры серии.
    """
    return Path(settings.get_str("download_tmp")) / ".subtitle-cache"


async def embedded_subtitle(file_id: int, index: int) -> bytes:
    """Достаёт дорожку субтитров из контейнера и отдаёт как ASS.

    Кэш обязателен: без него каждое открытие плеера заставляло бы ffmpeg заново
    пройти весь файл. Ключ включает `mtime` — файл могли заменить на месте под
    тем же именем, и старая дорожка к новому видео уже не подходит.
    """
    resolved = _resolve(file_id)
    if resolved is None:
        raise AudioError("Файл не найден")

    row, path = resolved
    if not path.exists():
        raise AudioError("Файл удалён с диска")
    if not ffmpeg_available():
        raise AudioError("В образе нет ffmpeg — вшитые субтитры не извлечь")

    mtime = int(path.stat().st_mtime)
    cached = _cache_dir() / f"{file_id}-{index}-{mtime}.ass"
    if cached.exists() and cached.stat().st_size > 0:
        return cached.read_bytes()

    from app.services.audio import _run

    cached.parent.mkdir(parents=True, exist_ok=True)
    code, _, stderr = await _run(
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        str(path),
        "-map",
        f"0:{index}",
        # Явный формат: без него ffmpeg выбирает по расширению, а расширение
        # у временного имени может смениться. Из SRT получится ASS без потерь —
        # оформления в SRT нет.
        "-f",
        "ass",
        str(cached),
    )
    if code != 0:
        cached.unlink(missing_ok=True)
        raise AudioError(
            f"ffmpeg не смог извлечь субтитры: {stderr.decode(errors='replace')[:300]}"
        )

    if not cached.exists() or cached.stat().st_size == 0:
        cached.unlink(missing_ok=True)
        raise AudioError("Дорожка извлеклась пустой")

    return cached.read_bytes()
