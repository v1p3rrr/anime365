"""Неудачная догрузка переводов обязана запоминаться.

Найдено на живом сервере, и выглядело это так: страница тайтла показывала
«подгружаются с Anime365…» **сутками**, а в лог каждые двадцать секунд падало
`Зеркало https://smotret-anime.org недоступно: ConnectTimeout`.

Сеть при этом была исправна. Проверено из того же контейнера десятью запросами:
через HTTP-прокси, через SOCKS5, с `http2=True` — и настоящий запрос переводов с
рабочим токеном шесть раз подряд, по 710 КБ за 0,4–0,6 с. Цикл событий тоже был
жив: опросы страницы шли каждые 1,5 с без задержек прямо сквозь таймаут.

Значит вечным спиннер делала не поломка сети, а устройство повтора:

1. клиент опрашивает страницу раз в 1,5 с, пока стоит `translations_pending`;
2. каждый опрос, если задачи нет, заводит новую;
3. задача падает за ~10 с и в `finally` снимает флаг;
4. следующий опрос заводит следующую — и так до бесконечности.

Один-единственный сбой превращался в бесконечный цикл запросов, вечный спиннер и
поток предупреждений. Тесты ниже держат исправление: неудача запоминается,
повтор откладывается с растущим отступом, а клиенту сообщается, что случилось.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import pytest

from app.services import title_service
from app.services.title_service import (
    REFRESH_RETRY_BASE,
    REFRESH_RETRY_CAP,
    RefreshFailure,
    forget_refresh_failure,
    schedule_translation_refresh,
    translation_refresh_failure,
)

SERIES = 40024


@pytest.fixture(autouse=True)
def _clean_state():
    """Состояние догрузки — модульное, между тестами его надо обнулять."""
    forget_refresh_failure()
    title_service._refreshing.clear()
    yield
    forget_refresh_failure()
    title_service._refreshing.clear()


def _run(series_id: int = SERIES) -> bool:
    """Заводит догрузку и дожидается, пока фоновая задача отработает.

    `asyncio.run`, а не маркер pytest-asyncio: плагина в проекте нет намеренно
    (он требует pytest<9), и корутины в тестах запускаются так же.
    """

    async def main() -> bool:
        started = schedule_translation_refresh(series_id)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return started

    return asyncio.run(main())


@pytest.fixture
def failing(monkeypatch):
    """Догрузка, которая падает так же, как падала на сервере."""
    calls: list[int] = []

    async def boom(series_id: int, force: bool = False):
        calls.append(series_id)
        raise ConnectionError("ConnectTimeout")

    monkeypatch.setattr(title_service, "ensure_details", boom)
    monkeypatch.setattr(title_service, "ensure_translations", boom)
    return calls


@pytest.fixture
def succeeding(monkeypatch):
    calls: list[int] = []

    async def fine(series_id: int, force: bool = False):
        calls.append(series_id)
        return []

    monkeypatch.setattr(title_service, "ensure_details", fine)
    monkeypatch.setattr(title_service, "ensure_translations", fine)
    return calls


class TestTheLoopIsBroken:
    def test_a_failure_is_remembered(self, failing):
        _run()
        failure = translation_refresh_failure(SERIES)
        assert failure is not None
        assert failure.attempts == 1
        assert "ConnectTimeout" in failure.message

    def test_the_next_attempt_is_not_immediate(self, failing):
        """Главное. Раньше следующий опрос через 1,5 с заводил новую попытку."""
        _run()
        assert len(failing) == 1

        assert _run() is False, "повтор пошёл сразу после неудачи — это тот самый цикл"
        assert len(failing) == 1, "в сеть сходили второй раз, хотя отступ не истёк"

    def test_ten_polls_cost_one_request(self, failing):
        """Клиент опрашивает раз в 1,5 с: за отступ набегает много опросов."""
        _run()
        for _ in range(10):
            _run()
        assert len(failing) == 1

    def test_after_the_backoff_it_tries_again(self, failing):
        _run()
        failure = translation_refresh_failure(SERIES)
        assert failure is not None

        # Отматываем время назад: ждать минуту в тесте незачем.
        title_service._failures[SERIES] = RefreshFailure(
            at=failure.at - REFRESH_RETRY_BASE - timedelta(seconds=1),
            attempts=failure.attempts,
            message=failure.message,
        )

        assert _run() is True
        assert len(failing) == 2

    def test_repeated_failures_raise_the_counter(self, failing):
        _run()
        for attempt in range(2, 5):
            current = translation_refresh_failure(SERIES)
            assert current is not None
            title_service._failures[SERIES] = RefreshFailure(
                at=current.at - REFRESH_RETRY_CAP - timedelta(seconds=1),
                attempts=current.attempts,
                message=current.message,
            )
            _run()
            assert translation_refresh_failure(SERIES).attempts == attempt


class TestBackoffArithmetic:
    def test_it_grows(self):
        delays = [
            RefreshFailure(at=datetime(2026, 1, 1), attempts=n, message="x").retry_at
            - datetime(2026, 1, 1)
            for n in (1, 2, 3, 4)
        ]
        assert delays == sorted(delays), "отступ обязан расти"
        assert delays[0] == REFRESH_RETRY_BASE

    def test_it_is_capped(self):
        """Иначе множитель разрастётся до переполнения timedelta."""
        far = RefreshFailure(at=datetime(2026, 1, 1), attempts=99, message="x")
        assert far.retry_at - datetime(2026, 1, 1) == REFRESH_RETRY_CAP


class TestRecovery:
    def test_success_forgets_the_failure(self, monkeypatch, failing):
        _run()
        assert translation_refresh_failure(SERIES) is not None

        async def fine(series_id: int, force: bool = False):
            return []

        monkeypatch.setattr(title_service, "ensure_details", fine)
        monkeypatch.setattr(title_service, "ensure_translations", fine)
        forget_refresh_failure(SERIES)  # как будто отступ истёк
        _run()

        assert translation_refresh_failure(SERIES) is None

    def test_a_person_does_not_wait_for_the_backoff(self, failing):
        """Кнопка «обновить» обязана срабатывать сразу, иначе выглядит сломанной."""
        _run()
        assert _run() is False

        forget_refresh_failure(SERIES)
        assert _run() is True
        assert len(failing) == 2

    def test_success_is_not_blocked_by_someone_elses_failure(self, succeeding):
        _run(SERIES)
        _run(SERIES + 1)
        assert len(succeeding) == 4  # по два вызова на тайтл: детали и переводы


class TestTheLogStopsDrowning:
    def test_only_the_first_failure_carries_a_traceback(self, failing, caplog):
        """Трассировка на каждый повтор — это и был поток в логе.

        На сервере предупреждение падало каждые двадцать секунд сутками. Первая
        трассировка нужна: без неё непонятно, что сломалось. Тысячная — нет.
        """
        with caplog.at_level(logging.WARNING, logger=title_service.log.name):
            _run()
            current = translation_refresh_failure(SERIES)
            title_service._failures[SERIES] = RefreshFailure(
                at=current.at - REFRESH_RETRY_CAP - timedelta(seconds=1),
                attempts=current.attempts,
                message=current.message,
            )
            _run()

        records = [r for r in caplog.records if "переводов тайтла" in r.getMessage()]
        assert len(records) == 2
        assert records[0].exc_info is not None, "первая неудача обязана объяснить себя"
        assert records[1].exc_info is None, "повтор не должен тащить трассировку"

    def test_the_repeat_says_when_it_will_try_again(self, failing, caplog):
        with caplog.at_level(logging.WARNING, logger=title_service.log.name):
            _run()
            current = translation_refresh_failure(SERIES)
            title_service._failures[SERIES] = RefreshFailure(
                at=current.at - REFRESH_RETRY_CAP - timedelta(seconds=1),
                attempts=current.attempts,
                message=current.message,
            )
            _run()

        assert "следующая не раньше" in caplog.records[-1].getMessage()
