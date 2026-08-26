"""`.gitignore` действительно закрывает файлы с секретами.

⚠️ Заведено по факту настоящей поломки, а не на всякий случай. Строка выглядела
безобидно:

    config/settings.yaml          # оверрайды из UI, у каждого свои

Git **не поддерживает хвостовые комментарии** в `.gitignore`: вся строка,
вместе с пробелами и текстом после `#`, считается одним литеральным именем
файла — и не совпадает ни с чем. То есть `config/settings.yaml` не игнорировался
вовсе, а в нём лежат оверрайды настроек из интерфейса, среди которых
`access_token` и `jellyfin_api_key`. Один `git add -A` после задания токена
через UI — и бессрочный премиум-токен уезжает в историю навсегда. Это ровно
правило №0.

Проверка текстовая, а не через `git check-ignore`: тесты гоняются и внутри
образа, где git не установлен.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GITIGNORE = REPO / ".gitignore"

#: Что обязано быть закрыто. Каждый — секрет или личные данные пользователя.
MUST_BE_IGNORED = (
    ".env",
    "config/settings.yaml",
    "config/session_secret",
    "data/",
    "temp/",
)


def _pattern_lines() -> list[str]:
    """Строки-шаблоны: без комментариев и пустых."""
    return [
        line
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


class TestNoTrailingComments:
    """Комментарий в конце строки превращает шаблон в мусор — молча."""

    def test_no_pattern_carries_a_trailing_comment(self) -> None:
        broken = [line for line in _pattern_lines() if "#" in line]

        assert broken == [], (
            "комментарий обязан стоять на отдельной строке: git считает "
            f"«путь # текст» одним именем файла, и шаблон перестаёт работать — {broken}"
        )

    def test_no_pattern_has_trailing_whitespace(self) -> None:
        """Хвостовой пробел тоже входит в шаблон и тоже ломает совпадение."""
        broken = [line for line in _pattern_lines() if line != line.rstrip()]

        assert broken == [], f"хвостовые пробелы в шаблонах: {broken}"


class TestSecretsAreCovered:
    def test_every_secret_path_has_an_exact_pattern(self) -> None:
        patterns = {line.strip() for line in _pattern_lines()}

        missing = [path for path in MUST_BE_IGNORED if path not in patterns]

        assert missing == [], f"не закрыто в .gitignore: {missing}"
