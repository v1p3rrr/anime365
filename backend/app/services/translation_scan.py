"""Фоновый сбор сведений о том, какие переводы есть у тайтла на сайте.

Зачем отдельная джоба. Списочная выдача каталога о переводах не знает вообще, а
серверного фильтра по ним нет: проверено на живом API — `chips` работает
(два жанра с `genre_op=and` честно сужают выдачу до 49 тайтлов), но поля для
типа перевода в нём не существует, и любой такой чип отдаёт то же, что заведомо
несуществующее поле. Значит единственный источник — `GET /api/translations`,
один запрос на тайтл, и собирать это можно только заранее и фоном.

Три вещи, ради которых здесь больше кода, чем кажется нужным:

1. **Не задудосить сайт.** Темп задаётся отдельно от пользовательских запросов
   и по умолчанию вдвое медленнее наблюдавшегося безопасного (~60 запросов в
   минуту). У фоновой джобы никто не ждёт результата, ей некуда спешить.
2. **Не держать базу.** SQLite допускает одного писателя, и длинная транзакция
   на весь проход заблокировала бы интерфейс на часы. Поэтому запись идёт
   короткими транзакциями по одному тайтлу, и ни одна не живёт дольше самой
   записи — сетевые вызовы всегда снаружи сессии.
3. **Пережить отказ.** Проход длинный, сеть за это время моргнёт не раз. Одна
   неудача на тайтле не должна ронять весь проход: такой тайтл просто останется
   с прежними данными и попадёт в следующий круг.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.db import session_scope
from app.core.events import bus
from app.models.catalog import ScanRun, Series, SeriesTranslations
from app.models.library import TitleMapping
from app.services.anime365 import Anime365Client, Anime365Error, SubscriptionRequired

log = logging.getLogger(__name__)

SCAN_KIND = "translations"

# Шесть типов, которые встречаются в данных. `voiceOth` и языки вне ru/en/ja
# сюда не попадают намеренно — они не участвуют ни в фильтрах, ни в значках,
# но учитываются в `total`, поэтому расхождение суммы и итога ожидаемо.
COUNTED_TYPES = ("sub_ja", "sub_ru", "sub_en", "raw_ja", "voice_ru", "voice_en")

# Сколько неудач подряд считать признаком, что дело не в тайтле, а в сайте.
# Один-два отказа бывают и на здоровом сервере (моргнула сеть, конкретная
# запись битая), а три подряд на разных тайтлах — это уже не совпадение.
FAILURES_MEANING_SITE_IS_DOWN = 3

# Пауза перед новой попыткой, когда похоже, что сайт лежит. Ретраить в таком
# случае немедленно бессмысленно: если сервер упал, за секунду он не встанет,
# а частые попытки только добавят ему нагрузки.
SITE_DOWN_RETRY_DELAY = 5 * 60.0

# Сколько таких пауз выдержать подряд. Шесть по пять минут — полчаса ожидания;
# если за полчаса сайт не ожил, проход честнее завершить и попробовать в
# следующий раз по расписанию, чем висеть до бесконечности.
SITE_DOWN_RETRIES = 6


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    """Настройка только из окружения, без UI и без реестра.

    Так решил пользователь: темп обхода и его расписание — вопрос эксплуатации
    конкретного сервера, а не вкуса, и менять их из браузера незачем.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("%s=%r не число, беру значение по умолчанию %s", name, raw, default)
        return default
    if value < minimum:
        log.warning("%s=%s меньше допустимого %s, беру %s", name, value, minimum, minimum)
        return minimum
    return value


def scan_interval_hours() -> int:
    """Как часто повторять проход. 0 отключает джобу целиком."""
    return _env_int("A365_TRANSLATION_SCAN_HOURS", 24)


def scan_rpm() -> int:
    """Запросов в минуту. По умолчанию вдвое медленнее наблюдавшегося безопасного."""
    return _env_int("A365_TRANSLATION_SCAN_RPM", 30, minimum=1)


def scan_scope() -> str:
    """`library` — только тайтлы с привязкой к папке, `catalog` — весь каталог.

    По умолчанию библиотека: это сотни тайтлов вместо двадцати девяти тысяч,
    то есть минуты вместо часов. Весь каталог включается осознанно и с
    пониманием, что первый проход будет долгим.
    """
    value = (os.environ.get("A365_TRANSLATION_SCAN_SCOPE") or "library").strip().lower()
    if value not in ("library", "catalog"):
        log.warning("A365_TRANSLATION_SCAN_SCOPE=%r не распознан, беру library", value)
        return "library"
    return value


def stale_after() -> timedelta:
    """Сколько данные считаются свежими. Проверенное недавно проход пропускает."""
    return timedelta(hours=max(1, scan_interval_hours()))


def counts_from_translations(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Считает переводы по шести типам.

    `typeKind` бывает `voice`, `sub`, `raw`, `voiceOth` и `subv`; `typeLang` —
    `ru`, `en`, `ja`, `er`. Сочетания вне списка игнорируем, но в `total` они
    входят: иначе «переводов 40, а по типам 12» выглядело бы как потеря данных.

    ⚠️ `subv` в счётчики намеренно НЕ идёт, хотя это тоже субтитры: там
    только надписи и песни, без реплик. Фильтр каталога «есть русские субтитры»
    обязан отвечать про перевод речи, иначе он привёл бы к тайтлу, где её никто
    не переводил.
    """
    counts = {key: 0 for key in COUNTED_TYPES}
    total = 0
    for item in items:
        total += 1
        key = f"{item.get('typeKind') or ''}_{item.get('typeLang') or ''}"
        if key in counts:
            counts[key] += 1
    counts["total"] = total
    return counts


class TranslationScan:
    """Проход по тайтлам со сбором доступных типов перевода."""

    def __init__(self) -> None:
        self._running = False
        self._cancel = False

    @property
    def running(self) -> bool:
        return self._running

    def cancel(self) -> None:
        """Просит проход остановиться после текущего тайтла."""
        self._cancel = True

    def _target_ids(self, scope: str, force: bool) -> list[int]:
        """Кого проверять. Уже свежие пропускаем, если не просили обратного."""
        with session_scope() as session:
            if scope == "catalog":
                stmt = select(Series.id)
            else:
                stmt = select(func.distinct(TitleMapping.series_id))
            ids = [row[0] for row in session.execute(stmt).all()]

            if force:
                return ids

            fresh_before = datetime.utcnow() - stale_after()
            fresh = {
                row[0]
                for row in session.execute(
                    select(SeriesTranslations.series_id).where(
                        SeriesTranslations.checked_at > fresh_before
                    )
                ).all()
            }
        return [series_id for series_id in ids if series_id not in fresh]

    @staticmethod
    def _save(series_id: int, counts: dict[str, int]) -> None:
        """Запись одного тайтла отдельной короткой транзакцией.

        Именно отдельной: держать одну на весь проход значит держать
        writer-lock SQLite часами, и всё остальное приложение встанет.
        """
        with session_scope() as session:
            stmt = sqlite_insert(SeriesTranslations).values(
                series_id=series_id, checked_at=datetime.utcnow(), **counts
            )
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[SeriesTranslations.series_id],
                    set_={
                        **{key: getattr(stmt.excluded, key) for key in (*COUNTED_TYPES, "total")},
                        "checked_at": stmt.excluded.checked_at,
                    },
                )
            )

    async def run(self, force: bool = False, limit: int | None = None) -> dict[str, Any]:
        if self._running:
            raise RuntimeError("Сбор сведений о переводах уже идёт")

        scope = scan_scope()
        rpm = scan_rpm()
        interval = 60.0 / rpm

        targets = self._target_ids(scope, force)
        if limit is not None:
            targets = targets[:limit]

        self._running = True
        self._cancel = False
        started = time.monotonic()
        checked = failed = 0

        with session_scope() as session:
            run_row = ScanRun(kind=SCAN_KIND, status="running")
            session.add(run_row)
            session.flush()
            run_id = run_row.id

        log.info(
            "Сбор сведений о переводах: %d тайтлов, охват %s, темп %d запросов в минуту",
            len(targets),
            scope,
            rpm,
        )

        try:
            # Один клиент на весь проход: у него внутри свой ограничитель, и
            # пересоздание на каждый тайтл сбрасывало бы отсчёт интервала.
            async with Anime365Client() as client:
                # Индекс двигаем вручную: при подозрении на лежащий сайт тот же
                # тайтл повторяется после паузы, а не пропускается.
                index = 0
                consecutive_failures = 0
                pauses_used = 0
                retry_queue: list[int] = []

                while index < len(targets):
                    if self._cancel:
                        log.info("Сбор сведений о переводах остановлен по запросу")
                        break

                    series_id = targets[index]
                    try:
                        items = await client.translations(series_id)
                    except SubscriptionRequired as exc:
                        # Дело не в сайте и не в тайтле: токен не годится, и
                        # следующие запросы упадут ровно так же. Ждать нечего.
                        raise Anime365Error(
                            f"Проход прекращён: {exc}. Проверьте токен в настройках."
                        ) from exc
                    except Anime365Error as exc:
                        consecutive_failures += 1
                        log.warning("Переводы тайтла %s не получены: %s", series_id, exc)

                        site_looks_down = consecutive_failures >= FAILURES_MEANING_SITE_IS_DOWN
                        if site_looks_down and pauses_used < SITE_DOWN_RETRIES:
                            pauses_used += 1
                            log.warning(
                                "Похоже, Anime365 недоступен (%d отказа подряд). "
                                "Ждём %.0f мин, попытка %d из %d",
                                consecutive_failures,
                                SITE_DOWN_RETRY_DELAY / 60,
                                pauses_used,
                                SITE_DOWN_RETRIES,
                            )
                            await asyncio.sleep(SITE_DOWN_RETRY_DELAY)
                            consecutive_failures = 0
                            # Тот же тайтл, индекс не двигаем.
                            continue

                        if site_looks_down:
                            raise Anime365Error(
                                f"Anime365 недоступен дольше "
                                f"{SITE_DOWN_RETRIES * SITE_DOWN_RETRY_DELAY / 60:.0f} минут — "
                                f"проход прекращён, проверено {checked} из {len(targets)}"
                            ) from exc

                        # Единичный отказ: тайтл откладываем на повторный заход
                        # в конце прохода. Пропустить насовсем было бы обидно —
                        # при первом запуске данных по нему нет вообще, и он
                        # остался бы пустым до завтра из-за секундного сбоя.
                        retry_queue.append(series_id)
                    else:
                        self._save(series_id, counts_from_translations(items))
                        checked += 1
                        # Сайт отвечает — бюджет пауз восстанавливается. Он про
                        # непрерывный простой, а не про сумму за весь проход.
                        consecutive_failures = 0
                        pauses_used = 0

                    if index % 25 == 0:
                        bus.publish(
                            "translation_scan",
                            done=index + 1,
                            total=len(targets),
                            failed=failed,
                        )

                    index += 1
                    # Пауза после запроса, а не до: последний тайтл не заставляет
                    # ждать впустую. Собственный темп, независимый от клиентского
                    # ограничителя, — тот рассчитан на запросы, которых ждёт человек.
                    if index < len(targets):
                        await asyncio.sleep(interval)

                # Второй и последний заход по отложенным. Дальше не повторяем:
                # если тайтл не дался дважды, дело в нём самом, и он подождёт
                # следующего прогона по расписанию.
                for series_id in retry_queue:
                    if self._cancel:
                        break
                    await asyncio.sleep(interval)
                    try:
                        items = await client.translations(series_id)
                    except Anime365Error as exc:
                        failed += 1
                        log.warning(
                            "Переводы тайтла %s не получены и со второй попытки: %s",
                            series_id,
                            exc,
                        )
                    else:
                        self._save(series_id, counts_from_translations(items))
                        checked += 1

            stats = {
                "checked": checked,
                "failed": failed,
                "retried": len(retry_queue),
                "scope": scope,
                "rpm": rpm,
                "seconds": round(time.monotonic() - started, 1),
            }
            self._finish(run_id, "ok", stats)
            log.info("Сбор сведений о переводах завершён: %s", stats)
            return stats

        except Exception as exc:
            self._finish(run_id, "failed", {"checked": checked, "failed": failed}, str(exc))
            raise
        finally:
            self._running = False
            bus.publish("translation_scan", done=checked, total=len(targets))

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


translation_scan = TranslationScan()
