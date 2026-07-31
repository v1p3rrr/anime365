"""Ошибки, приходящие В ТЕЛЕ ответа со статусом 200.

Anime365 сообщает о беде двумя способами: статусом и телом при 200. Второй
легко просмотреть — и тогда любая ошибка выглядит как «сайт недоступен, смените
зеркало». Совет неверный для всех случаев, кроме одного, поэтому тесты
разбирают именно эту развилку.
"""

from __future__ import annotations

import pytest

from app.core.errors import upstream_unavailable
from app.services.anime365 import (
    Anime365Error,
    RateLimited,
    SubscriptionRequired,
    TokenMissing,
    _error_from_payload,
    _payload_error_code,
)


class TestCodeExtraction:
    def test_reads_numeric_code(self):
        assert _payload_error_code({"code": 404, "message": "нет"}) == 404

    def test_string_body_has_no_code(self):
        # Ошибка приходит и просто строкой — падать на этом нельзя.
        assert _payload_error_code("что-то пошло не так") is None

    def test_non_numeric_code_is_ignored(self):
        assert _payload_error_code({"code": "NOT_FOUND"}) is None


class TestTokenCases:
    def test_missing_token_is_named_directly(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "get_str", lambda key, *a: "" if key == "access_token" else "x")
        error = _error_from_payload({"code": 403, "message": "You should login first."}, "/x")
        assert isinstance(error, TokenMissing)

    def test_rejected_token_points_at_the_subscription(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "get_str", lambda key, *a: "есть-токен")
        error = _error_from_payload({"code": 403, "message": "You should login first."}, "/x")
        assert isinstance(error, SubscriptionRequired)

    def test_login_wording_alone_is_enough(self, monkeypatch):
        # Код может отличаться, а смысл тот же — опираться только на 403 нельзя.
        from app.core.config import settings

        monkeypatch.setattr(settings, "get_str", lambda key, *a: "есть-токен")
        error = _error_from_payload({"code": 401, "message": "You should login first."}, "/x")
        assert isinstance(error, SubscriptionRequired)


class TestMappingToApiErrors:
    @pytest.mark.parametrize(
        "exc,status,code",
        [
            (TokenMissing("нет токена"), 403, "token_missing"),
            (SubscriptionRequired("не принят"), 403, "subscription_required"),
            (Anime365Error("таймаут"), 502, "upstream_unavailable"),
        ],
    )
    def test_each_case_gets_its_own_answer(self, exc, status, code):
        result = upstream_unavailable(exc)
        assert result.status_code == status
        assert result.detail["code"] == code

    def test_rate_limit_is_not_a_failure(self):
        # RateLimited наследует Anime365Error, но обрабатывается отступом выше
        # по стеку и до превращения в ответ API доходить не должен.
        assert issubclass(RateLimited, Anime365Error)
