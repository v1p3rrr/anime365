"""Словари ограничителей не растут без предела.

⚠️ Ключ у них задаёт обращающийся: у счётчика по имени это логин из тела
запроса, а `/api/auth/login` открыт без сессии. Пока ключи не удалялись,
тысяча выдуманных имён давала тысячу вечных записей — при лимите памяти
контейнера 512–768 МБ это дорога в OOM и перезапуск.

Отдельная тонкость, из-за которой протечка и жила незаметно: `check()`
записывал в словарь **пустой список**, а для самого `check()` пустой список и
отсутствующий ключ неотличимы (`self._attempts.get(key, [])`). То есть
поведение было верным, а память утекала.
"""

from __future__ import annotations

import time

from app.core.security import PRUNE_THRESHOLD, AttemptLimiter, RateLimiter


class TestAttemptLimiterForgets:
    def test_a_checked_key_without_hits_is_dropped(self) -> None:
        limiter = AttemptLimiter()

        assert limiter.check("кто-то") == 0.0

        assert "кто-то" not in limiter._attempts

    def test_a_key_survives_while_its_failures_are_fresh(self) -> None:
        """Уборка не должна обнулять счётчик тому, кто ещё в своём окне."""
        limiter = AttemptLimiter()
        limiter.register_failure("подбирающий")

        limiter.check("подбирающий")

        assert limiter._attempts["подбирающий"]

    def test_the_limit_still_works_after_pruning(self) -> None:
        limiter = AttemptLimiter(limit=3, window=300.0)
        for _ in range(3):
            limiter.register_failure("настойчивый")

        assert limiter.check("настойчивый") > 0

    def test_stale_keys_are_swept_once_there_are_many(self) -> None:
        limiter = AttemptLimiter(limit=10, window=300.0, max_window=300.0)

        # Протухшие: отметка давно вне самого широкого окна.
        long_ago = time.time() - 10_000
        for n in range(PRUNE_THRESHOLD + 5):
            limiter._attempts[f"старый-{n}"] = [long_ago]

        # Любая новая неудача запускает уборку, раз ключей стало много.
        limiter.register_failure("свежий")

        assert len(limiter._attempts) < PRUNE_THRESHOLD
        assert "свежий" in limiter._attempts

    def test_sweeping_keeps_the_fresh_ones(self) -> None:
        limiter = AttemptLimiter(limit=10, window=300.0, max_window=300.0)
        long_ago = time.time() - 10_000
        for n in range(PRUNE_THRESHOLD + 5):
            limiter._attempts[f"старый-{n}"] = [long_ago]
        limiter._attempts["живой"] = [time.time()]

        limiter.register_failure("свежий")

        assert "живой" in limiter._attempts


class TestRateLimiterForgets:
    def test_stale_keys_are_swept(self) -> None:
        limiter = RateLimiter()
        long_ago = time.time() - RateLimiter.STALE_AFTER - 100
        for n in range(PRUNE_THRESHOLD + 5):
            limiter._hits[f"старый-{n}"] = [long_ago]

        assert limiter.allow("свежий", limit=10, window=60.0)

        assert len(limiter._hits) < PRUNE_THRESHOLD

    def test_the_limit_still_works(self) -> None:
        limiter = RateLimiter()
        for _ in range(3):
            assert limiter.allow("гость", limit=3, window=60.0)

        assert not limiter.allow("гость", limit=3, window=60.0)

    def test_a_recent_key_is_not_swept(self) -> None:
        """Час простоя — порог с запасом: окна у вызывающих минутные."""
        limiter = RateLimiter()
        for n in range(PRUNE_THRESHOLD + 5):
            limiter._hits[f"старый-{n}"] = [time.time() - 10_000]
        limiter._hits["минуту назад"] = [time.time() - 60]

        limiter.allow("свежий", limit=10, window=60.0)

        assert "минуту назад" in limiter._hits
