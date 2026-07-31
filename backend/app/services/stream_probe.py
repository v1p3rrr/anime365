"""Определение звуковых дорожек через ffprobe — фоновой джобой.

Почему не по умолчанию и почему не в живом запросе.

Замерено: ffprobe читает не файл целиком, а заголовок с индексом — 0.37 МБ из
37 МБ. Дорог не объём, а характер обращений: на каждый файл приходится
`fork/exec` (45–50 мс сами по себе) и пара мелких чтений вразнобой. На диске
5400 rpm, с которого одновременно качают и стримят, эти перемещения головки
отбираются у активных загрузок — и заметно это будет именно им, а не нам.

Отсюда три решения:

1. **Под тумблером.** У кого библиотека размечена Jellyfin, `<streamdetails>`
   в `.nfo` уже есть, и ffprobe не нужен вовсе.
2. **В фоне, а не в запросе.** Пользователь не должен ждать обхода файлов.
3. **Результат в базе, повторно не трогаем.** Проверенный и не изменившийся
   файл при следующем скане пропускается. Это превращает разовую цену в
   разовую, а не в цену каждого прохода.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.db import session_scope
from app.core.events import bus
from app.models.catalog import ScanRun
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot

log = logging.getLogger(__name__)

SCAN_KIND = "ffprobe"

# Пауза между файлами. Не ради вежливости к процессору, а ради диска: без неё
# джоба выстраивает сплошную очередь мелких случайных чтений и конкурирует с
# загрузками, идущими на тот же шпиндель. Четверть секунды даёт очереди диска
# продохнуть, а на цену прохода влияет умеренно.
PAUSE_BETWEEN_FILES = 0.25

# Сколько файлов обрабатывать за один проход. Ограничение на случай, когда
# библиотеку добавили целиком: лучше растянуть на несколько ночей, чем занять
# диск на час подряд.
MAX_FILES_PER_RUN = 2000


class StreamProbe:
    """Обход файлов без сведений о дорожках."""

    def __init__(self) -> None:
        self._running = False
        self._cancel = False

    @property
    def running(self) -> bool:
        return self._running

    def cancel(self) -> None:
        self._cancel = True

    @staticmethod
    def _candidates(series_id: int | None = None, force: bool = False) -> list[dict[str, Any]]:
        """Какие файлы проверять.

        Пропускаем всё, про что уже знаем:

        * есть `<streamdetails>` из `.nfo` — они достались даром и надёжнее,
          чем повторный разбор;
        * файл уже проверяли, и `mtime` с тех пор не менялся.

        `force` снимает второе условие — это и есть «жёсткий перескан» для
        случая, когда файлы подменили в обход отметок времени.
        """
        with session_scope() as session:
            stmt = (
                select(
                    LibraryFile.id,
                    LibraryFile.filename,
                    LibraryFile.streams,
                    LibraryFile.mtime,
                    LibraryFile.probed_at,
                    LibraryFile.probed_mtime,
                    LibraryFolder.rel_path,
                    LibraryRoot.path,
                )
                .join(LibraryFolder, LibraryFile.folder_id == LibraryFolder.id)
                .join(LibraryRoot, LibraryFolder.root_id == LibraryRoot.id)
                .where(LibraryFile.kind == "video", LibraryRoot.is_available.is_(True))
            )
            if series_id is not None:
                from app.models.library import TitleMapping

                stmt = stmt.where(
                    LibraryFile.folder_id.in_(
                        select(TitleMapping.folder_id).where(
                            TitleMapping.series_id == series_id
                        )
                    )
                )
            rows = session.execute(stmt).all()

        out: list[dict[str, Any]] = []
        for row in rows:
            # ⚠️ Отбор по «нет данных» — в Python, а не условием в SQL.
            # SQLAlchemy пишет Python None в JSON-колонку строкой 'null', а не
            # SQL NULL, поэтому `streams.is_(None)` не совпадает ни с чем.
            if row.streams:
                continue
            if not force and row.probed_at is not None and row.probed_mtime == row.mtime:
                # Уже смотрели этот самый файл и ничего не нашли — незачем
                # открывать его снова на каждом скане.
                continue
            out.append(
                {
                    "id": row.id,
                    "filename": row.filename,
                    "path": Path(row.path) / row.rel_path / row.filename,
                    "mtime": row.mtime,
                }
            )
        return out

    @staticmethod
    def _save(file_id: int, streams: dict[str, Any] | None, mtime: float | None) -> None:
        """Отметка о проверке ставится всегда, даже когда дорожек не нашлось.

        Иначе файл без распознаваемых дорожек попадал бы в очередь снова и
        снова — а это ровно тот случай, ради которого отметка и заведена.
        """
        with session_scope() as session:
            row = session.get(LibraryFile, file_id)
            if row is None:
                return
            if streams and (streams.get("audio_languages") or streams.get("subtitle_languages")):
                row.streams = streams
            row.probed_at = datetime.utcnow()
            row.probed_mtime = mtime

    async def run(
        self, series_id: int | None = None, force: bool = False, limit: int | None = None
    ) -> dict[str, Any]:
        from app.services.audio import AudioError, ffmpeg_available, probe_stream_languages

        if self._running:
            raise RuntimeError("Определение дорожек уже идёт")
        if not settings.get_bool("enable_ffprobe"):
            return {"skipped": "выключено в настройках", "probed": 0}
        if not ffmpeg_available():
            return {"skipped": "в образе нет ffmpeg", "probed": 0}

        candidates = self._candidates(series_id, force)[: limit or MAX_FILES_PER_RUN]
        if not candidates:
            return {"probed": 0, "failed": 0, "total": 0}

        self._running = True
        self._cancel = False
        started = datetime.utcnow()
        probed = failed = 0

        with session_scope() as session:
            run_row = ScanRun(kind=SCAN_KIND, status="running")
            session.add(run_row)
            session.flush()
            run_id = run_row.id

        log.info("Определение дорожек: файлов к проверке %d", len(candidates))

        try:
            for index, item in enumerate(candidates):
                if self._cancel:
                    log.info("Определение дорожек остановлено по запросу")
                    break

                path: Path = item["path"]
                if not path.is_file():
                    # Файл исчез между сканом и проверкой — отмечаем, чтобы не
                    # возвращаться к нему каждый проход.
                    self._save(item["id"], None, item["mtime"])
                    failed += 1
                    continue

                try:
                    streams = await probe_stream_languages(path)
                except AudioError as exc:
                    failed += 1
                    log.warning("ffprobe не осилил %s: %s", item["filename"], exc)
                    self._save(item["id"], None, item["mtime"])
                else:
                    self._save(item["id"], streams, item["mtime"])
                    probed += 1

                if index % 50 == 0:
                    bus.publish("stream_probe", done=index + 1, total=len(candidates))

                if index + 1 < len(candidates):
                    await asyncio.sleep(PAUSE_BETWEEN_FILES)

            seconds = (datetime.utcnow() - started).total_seconds()
            stats = {
                "probed": probed,
                "failed": failed,
                "total": len(candidates),
                "seconds": round(seconds, 1),
                # Замер на реальном железе: по нему видно, стоит ли гонять
                # джобу чаще или, наоборот, реже.
                "seconds_per_file": round(seconds / max(probed + failed, 1), 3),
            }
            self._finish(run_id, "ok", stats)
            log.info("Определение дорожек завершено: %s", stats)
            return stats

        except Exception as exc:
            self._finish(run_id, "failed", {"probed": probed, "failed": failed}, str(exc))
            raise
        finally:
            self._running = False
            bus.publish("stream_probe", done=probed, total=len(candidates))

    @staticmethod
    def _finish(
        run_id: int, status: str, stats: dict[str, Any], error: str | None = None
    ) -> None:
        with session_scope() as session:
            row = session.get(ScanRun, run_id)
            if row is not None:
                row.status = status
                row.finished_at = datetime.utcnow()
                row.stats = stats
                row.error = error


stream_probe = StreamProbe()
