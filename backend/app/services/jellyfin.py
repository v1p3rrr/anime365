"""Пинок Jellyfin: «пересканируй библиотеку» после того, как мы положили файлы.

Единственная операция интеграции. Всё остальное Jellyfin делает сам, и лезть
туда незачем — иначе пришлось бы поддерживать его модель данных.

Важно: зовём обновление **всей** библиотеки, а не конкретной папки. Обновление
папки требует пути в терминах Jellyfin, а он видит тома под своими путями
(`/media/anime` у нас против чего угодно у него) — понадобилась бы таблица
трансляции, которую пользователю пришлось бы заполнять руками и держать в
согласии с двумя docker-compose. Полное обновление у Jellyfin инкрементальное:
он сам смотрит только изменившееся.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

# Пауза перед запросом. Батч из полусотни серий завершается пачкой, и дёргать
# Jellyfin на каждую — значит запустить полсотни сканирований подряд.
DEBOUNCE_SECONDS = 30


class JellyfinNotifier:
    def __init__(self) -> None:
        self._pending: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(
            settings.get_bool("enable_jellyfin")
            and settings.get_str("jellyfin_url")
            and settings.get_str("jellyfin_api_key")
        )

    def schedule(self) -> None:
        """Просит обновление, схлопывая частые вызовы в один."""
        if not self.enabled:
            return

        if self._pending is not None and not self._pending.done():
            # Уже запланировано — новая загрузка попадёт в то же обновление.
            return

        try:
            self._pending = asyncio.get_running_loop().create_task(self._run_later())
        except RuntimeError:
            # Вызвано вне событийного цикла (например, из синхронного кода
            # тестов) — тогда просто ничего не планируем.
            log.debug("Обновление Jellyfin не запланировано: нет активного цикла")

    async def _run_later(self) -> None:
        await asyncio.sleep(DEBOUNCE_SECONDS)
        await self.refresh()

    async def refresh(self) -> dict[str, object]:
        """Запускает сканирование библиотеки в Jellyfin.

        Возвращает результат, чтобы кнопкой «проверить» из настроек можно было
        убедиться, что адрес и ключ рабочие, а не гадать по логам.
        """
        base = settings.get_str("jellyfin_url").rstrip("/")
        key = settings.get_str("jellyfin_api_key")
        if not base or not key:
            return {"ok": False, "reason": "Не заданы адрес или ключ Jellyfin"}

        url = f"{base}/Library/Refresh"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, headers={"X-Emby-Token": key})
        except httpx.HTTPError as exc:
            detail = str(exc) or type(exc).__name__
            log.warning("Jellyfin недоступен: %s", detail)
            return {"ok": False, "reason": f"Jellyfin недоступен: {detail}"}

        if response.status_code == 401:
            return {"ok": False, "reason": "Jellyfin не принял API-ключ"}
        if response.status_code >= 400:
            return {"ok": False, "reason": f"Jellyfin ответил {response.status_code}"}

        log.info("Jellyfin получил запрос на обновление библиотеки")
        return {"ok": True, "status": response.status_code}


jellyfin = JellyfinNotifier()
