"""Тесты того, что может сломаться незаметно.

Здесь не «покрытие ради покрытия»: каждый случай — это либо дыра в безопасности,
либо поведение, выясненное экспериментом с живым API, которое легко потерять
при рефакторинге.
"""

from __future__ import annotations

import pytest

from app.core.settings_registry import ValidationError, get
from app.services.fts import build_query


class TestFtsEscaping:
    """Пользовательский ввод не должен попадать в MATCH-выражение как есть."""

    @pytest.mark.parametrize(
        "user_input",
        [
            '" OR 1=1 --',
            'x") UNION SELECT',
            "NEAR(a b)",
            "a* AND b*",
            'title:"exact"',
            "^anchor",
            "-negation",
        ],
    )
    def test_operators_never_survive(self, user_input: str) -> None:
        query = build_query(user_input)
        if query is None:
            return
        # Каждый токен обёрнут в кавычки; вне кавычек допустимы только AND и *.
        bare = query.replace('"', "").replace("*", "").replace(" AND ", " ")
        assert not any(ch in bare for ch in "():^-"), query

    def test_internal_quotes_are_doubled(self) -> None:
        # В FTS5 кавычка внутри строки экранируется удвоением.
        assert build_query('he"llo') == '"he"* AND "llo"*'

    def test_empty_input_gives_none(self) -> None:
        assert build_query("") is None
        assert build_query("   ") is None
        assert build_query("!!!") is None

    def test_cyrillic_survives(self) -> None:
        assert build_query("Фрирен") == '"Фрирен"*'

    def test_prefix_search_enabled(self) -> None:
        # «фрир» обязано находить «Фрирен», иначе поиск бесполезен.
        assert build_query("фрир").endswith("*")

    def test_token_count_is_capped(self) -> None:
        query = build_query(" ".join(f"w{i}" for i in range(100)))
        assert query is not None
        assert query.count(" AND ") <= 11


class TestSettingsValidation:
    def test_base_url_rejects_foreign_host(self) -> None:
        # Ровно тот сценарий, про который спрашивал пользователь: правка значения
        # на стороне браузера не должна открывать путь на посторонний хост.
        with pytest.raises(ValidationError):
            get("base_url").validate("https://evil.example.com")

    def test_base_url_rejects_private_ip(self) -> None:
        with pytest.raises(ValidationError):
            get("base_url").validate("http://192.168.1.1")

    def test_known_mirror_passes(self) -> None:
        assert get("base_url").validate("https://smotret-anime.org")

    def test_concurrency_allows_zero_but_not_negative(self) -> None:
        setting = get("max_concurrent_downloads")
        assert setting.validate(0) == 0  # 0 означает «без ограничения»
        with pytest.raises(ValidationError):
            setting.validate(-1)

    def test_bad_int_is_rejected_on_parse(self) -> None:
        with pytest.raises(ValidationError):
            get("preferred_height").parse("не число")

    def test_path_must_be_absolute(self) -> None:
        with pytest.raises(ValidationError):
            get("series_root").validate("relative/path")

    def test_bool_parsing_accepts_common_forms(self) -> None:
        setting = get("watch_filesystem")
        assert setting.parse("true") is True
        assert setting.parse("1") is True
        assert setting.parse("off") is False
        with pytest.raises(ValidationError):
            setting.parse("maybe")


class TestHostRewrite:
    """embed всегда отдаёт ссылки на smotret-anime.org, независимо от зеркала.

    Без переписывания хоста при блокировке .org каталог бы работал,
    а загрузки молча падали.
    """

    def test_known_mirror_is_rewritten(self) -> None:
        from app.services.anime365 import Anime365Client

        result = Anime365Client.rewrite_host(
            "https://smotret-anime.org/translations/mp4/4505262?download=1&height=720"
        )
        assert result.startswith("https://smotret-anime.app/")
        assert "download=1&height=720" in result

    def test_relative_subtitles_url_gets_origin(self) -> None:
        from app.services.anime365 import Anime365Client

        result = Anime365Client.rewrite_host("/episodeTranslations/4505262.ass?willcache")
        assert result == "https://smotret-anime.app/episodeTranslations/4505262.ass?willcache"

    def test_cdn_host_is_left_alone(self) -> None:
        from app.services.anime365 import Anime365Client

        # CDN-ссылки подписанные и живут на своих хостах — трогать их нельзя.
        cdn = "https://ru-549959.quantum-phantom-moon.ru/s/q1561620/route/?m=1&r=abc"
        assert Anime365Client.rewrite_host(cdn) == cdn


class TestSecretRedaction:
    def test_token_is_removed_from_log_text(self) -> None:
        from app.core.logging_setup import SecretRedactingFilter

        # Значение синтетическое. Настоящие токены не попадают ни в тесты, ни в фикстуры,
        # ни в примеры — см. правило №0 в claude/07-security.md.
        token = "0" * 32 + "deadbeef"
        text = f"GET /api/me?access_token={token} failed"
        assert token not in SecretRedactingFilter._redact(text, (token,))

    def test_short_values_are_not_redacted(self) -> None:
        from app.core.logging_setup import SecretRedactingFilter

        # Иначе короткий секрет вроде "1" изуродовал бы все сообщения.
        assert SecretRedactingFilter._redact("all fine", ("ok",)) == "all fine"
