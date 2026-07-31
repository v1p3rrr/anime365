"""Фоновый сбор сведений о доступных переводах.

Главное здесь не «посчитали правильно», а «пережили отказ и не задудосили сайт».
Проход длинный: на весь каталог это часы, за которые сеть моргнёт не раз, а сайт
может и полежать. Поэтому тесты в основном про поведение при ошибках.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import translation_scan as ts
from app.services.anime365 import Anime365Error, SubscriptionRequired


class TestCounting:
    def test_counts_by_six_types(self):
        counts = ts.counts_from_translations(
            [
                {"typeKind": "sub", "typeLang": "ja"},
                {"typeKind": "sub", "typeLang": "ja"},
                {"typeKind": "voice", "typeLang": "ru"},
                {"typeKind": "raw", "typeLang": "ja"},
            ]
        )
        assert counts["sub_ja"] == 2
        assert counts["voice_ru"] == 1
        assert counts["raw_ja"] == 1
        assert counts["total"] == 4

    def test_other_languages_count_in_total_only(self):
        # Украинская озвучка и voiceOth в шесть типов не входят, но это переводы,
        # и в total они обязаны попасть — иначе «всего 3, по типам 1» читалось бы
        # как потеря данных.
        counts = ts.counts_from_translations(
            [
                {"typeKind": "voiceOth", "typeLang": "er"},
                {"typeKind": "voice", "typeLang": "uk"},
                {"typeKind": "sub", "typeLang": "ru"},
            ]
        )
        assert counts["total"] == 3
        assert sum(counts[key] for key in ts.COUNTED_TYPES) == 1

    def test_garbage_does_not_crash(self):
        counts = ts.counts_from_translations([{"typeKind": None, "typeLang": None}, {}])
        assert counts["total"] == 2
        assert all(counts[key] == 0 for key in ts.COUNTED_TYPES)


class TestEnvSettings:
    def test_defaults(self, monkeypatch):
        for name in (
            "A365_TRANSLATION_SCAN_HOURS",
            "A365_TRANSLATION_SCAN_RPM",
            "A365_TRANSLATION_SCAN_SCOPE",
        ):
            monkeypatch.delenv(name, raising=False)
        assert ts.scan_interval_hours() == 24
        assert ts.scan_rpm() == 30
        assert ts.scan_scope() == "library"

    def test_zero_hours_disables(self, monkeypatch):
        monkeypatch.setenv("A365_TRANSLATION_SCAN_HOURS", "0")
        assert ts.scan_interval_hours() == 0

    def test_garbage_falls_back_to_default(self, monkeypatch):
        # Опечатка в docker-compose не должна ронять запуск контейнера.
        monkeypatch.setenv("A365_TRANSLATION_SCAN_RPM", "быстро")
        assert ts.scan_rpm() == 30

    def test_rpm_never_drops_below_one(self, monkeypatch):
        # Ноль означал бы деление на ноль при расчёте паузы.
        monkeypatch.setenv("A365_TRANSLATION_SCAN_RPM", "0")
        assert ts.scan_rpm() == 1

    def test_unknown_scope_falls_back_to_library(self, monkeypatch):
        monkeypatch.setenv("A365_TRANSLATION_SCAN_SCOPE", "всё подряд")
        assert ts.scan_scope() == "library"


class FakeClient:
    """Подменяет клиент API: отдаёт заранее заданный сценарий ответов."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[int] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def translations(self, series_id: int):
        self.calls.append(series_id)
        item = self.script.pop(0) if self.script else []
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fast_scan(monkeypatch):
    """Убирает все паузы: тест про логику, а не про ожидание.

    Настоящий `sleep` берём ДО подмены: `ts.asyncio` — это сам модуль asyncio,
    и заглушка, зовущая `asyncio.sleep`, звала бы саму себя до переполнения стека.
    """
    real_sleep = asyncio.sleep

    async def instant(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(ts.asyncio, "sleep", instant)
    monkeypatch.setattr(ts, "SITE_DOWN_RETRY_DELAY", 0.0)
    saved: list[tuple[int, dict]] = []
    monkeypatch.setattr(ts.TranslationScan, "_save", staticmethod(
        lambda series_id, counts: saved.append((series_id, counts))
    ))
    monkeypatch.setattr(ts.TranslationScan, "_finish", staticmethod(lambda *a, **k: None))
    return saved


def _run(scan, monkeypatch, client, targets, **kwargs):
    monkeypatch.setattr(ts, "Anime365Client", lambda: client)
    monkeypatch.setattr(ts.TranslationScan, "_target_ids", lambda self, scope, force: targets)
    monkeypatch.setattr(ts, "session_scope", _null_session)
    return asyncio.run(scan.run(**kwargs))


class _null_session:
    """Заглушка сессии: запись и учёт прогона в этих тестах не проверяются."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def add(self, _obj):
        pass

    def flush(self):
        pass

    def get(self, *_a):
        return None

    def execute(self, *_a):
        raise AssertionError("обращения к БД в этих тестах не ожидаются")


class TestSurvivingFailures:
    def test_single_failure_skips_one_title_and_continues(self, monkeypatch, fast_scan):
        client = FakeClient([
            [{"typeKind": "sub", "typeLang": "ja"}],
            Anime365Error("сеть моргнула"),
            [{"typeKind": "voice", "typeLang": "ru"}],
        ])
        stats = _run(ts.TranslationScan(), monkeypatch, client, [1, 2, 3])

        # Тайтл 2 отложен и повторён в конце прохода — на второй попытке
        # сценарий отдаёт пустой список, то есть запрос удался.
        assert stats["checked"] == 3
        assert stats["retried"] == 1
        assert client.calls == [1, 2, 3, 2]

    def test_three_failures_in_a_row_trigger_pause_and_retry(self, monkeypatch, fast_scan):
        # Три подряд — признак, что лежит сайт, а не битый тайтл. После паузы
        # тот же тайтл повторяется, а не пропускается.
        client = FakeClient([
            Anime365Error("нет связи"),
            Anime365Error("нет связи"),
            Anime365Error("нет связи"),
            [{"typeKind": "sub", "typeLang": "ru"}],
            [{"typeKind": "sub", "typeLang": "ru"}],
            [{"typeKind": "sub", "typeLang": "ru"}],
        ])
        stats = _run(ts.TranslationScan(), monkeypatch, client, [1, 2, 3])

        # Третий отказ подряд вызвал паузу и повтор того же тайтла, а первые
        # два ушли в отложенные и разобрались в конце прохода.
        assert stats["checked"] == 3
        assert client.calls == [1, 2, 3, 3, 1, 2]

    def test_gives_up_after_the_retry_budget(self, monkeypatch, fast_scan):
        # Пауза срабатывает на каждый третий отказ подряд, поэтому чтобы
        # исчерпать бюджет из двух пауз, тайтлов нужно с запасом.
        monkeypatch.setattr(ts, "SITE_DOWN_RETRIES", 2)
        client = FakeClient([Anime365Error("сайт лежит")] * 100)

        with pytest.raises(Anime365Error, match="недоступен дольше"):
            _run(ts.TranslationScan(), monkeypatch, client, list(range(1, 13)))

        # Висеть вечно нельзя: бюджет пауз ограничен, дальше проход честно падает,
        # не дойдя до конца списка.
        assert len(client.calls) < 100

    def test_our_own_internet_dropping_is_handled_like_site_downtime(
        self, monkeypatch, fast_scan
    ):
        """Пропал интернет на нашем сервере — не отличается от «сайт лежит».

        Клиент в этом случае перебирает все зеркала и ни от одного не получает
        ответа, поэтому наружу выходит тот же `Anime365Error`. Реакция должна
        быть той же: подождать и повторить, а не пропустить тайтлы. Отдельный
        тест ради того, чтобы это осталось гарантией, а не совпадением.
        """
        no_network = Anime365Error(
            "Ни одно зеркало не ответило на /api/translations. "
            "Последняя ошибка — ConnectError: [Errno -3] Temporary failure in name resolution"
        )
        client = FakeClient([no_network, no_network, no_network, [], [], []])
        stats = _run(ts.TranslationScan(), monkeypatch, client, [1, 2, 3])

        # Ничего не потеряно: третий повторён после паузы, первые два разобраны
        # отложенным заходом.
        assert stats["checked"] == 3
        assert stats["failed"] == 0

    def test_bad_token_aborts_immediately(self, monkeypatch, fast_scan):
        # Дело не в сайте: следующие запросы упадут так же, ждать бессмысленно.
        client = FakeClient([SubscriptionRequired("403")] + [[]] * 10)

        with pytest.raises(Anime365Error, match="Проверьте токен"):
            _run(ts.TranslationScan(), monkeypatch, client, [1, 2, 3])

        assert client.calls == [1]

    def test_recovery_restores_the_pause_budget(self, monkeypatch, fast_scan):
        # Бюджет пауз — про непрерывный простой, а не про сумму за проход:
        # сайт, который моргает весь день, не должен исчерпать его к обеду.
        monkeypatch.setattr(ts, "SITE_DOWN_RETRIES", 1)
        client = FakeClient([
            Anime365Error("x"), Anime365Error("x"), Anime365Error("x"),
            [],  # ожил — бюджет восстановился
            Anime365Error("x"), Anime365Error("x"), Anime365Error("x"),
            [], [], [],
        ])
        stats = _run(ts.TranslationScan(), monkeypatch, client, [1, 2, 3, 4])
        assert stats["checked"] >= 2


class TestPacing:
    def test_pause_between_requests_matches_the_rate(self, monkeypatch, fast_scan):
        monkeypatch.setenv("A365_TRANSLATION_SCAN_RPM", "20")
        delays: list[float] = []

        async def record(seconds):
            delays.append(seconds)

        monkeypatch.setattr(ts.asyncio, "sleep", record)
        client = FakeClient([[], [], []])
        _run(ts.TranslationScan(), monkeypatch, client, [1, 2, 3])

        # 20 запросов в минуту — три секунды между ними.
        assert delays == [3.0, 3.0]

    def test_no_pause_after_the_last_title(self, monkeypatch, fast_scan):
        delays: list[float] = []

        async def record(seconds):
            delays.append(seconds)

        monkeypatch.setattr(ts.asyncio, "sleep", record)
        client = FakeClient([[]])
        _run(ts.TranslationScan(), monkeypatch, client, [1])

        # Пауза после последнего запроса задержала бы завершение прохода впустую.
        assert delays == []
