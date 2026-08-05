"""В `.env.example` каждая переменная должна встречаться один раз.

Повод: в рабочем `.env.app` на сервере `A365_UI_PASSWORD` оказался задан дважды
(строка 49 — `admin`, строка 117 — значение из одного символа). Побеждает
последнее, и вход с ожидаемым паролем не работал; на поиск причины ушло время,
потому что глазами файл выглядел правильным — первое вхождение было там, где
его и ждёшь.

**Проверено: генератор из реестра тут ни при чём — дублей он не создаёт.**
Тест оставлен сторожем: реестр правят руками, и пара «настройка + запись в
`ENV_ONLY` с тем же именем» даст ровно такой файл, а заметить это по диффу
почти невозможно.
"""

from __future__ import annotations

from collections import Counter

from app.core.settings_registry import ENV_ONLY, SETTINGS, render_env_example


def _assignments(text: str) -> list[str]:
    """Имена переменных из строк вида `ИМЯ=значение`, без комментариев."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        out.append(stripped.split("=", 1)[0].strip())
    return out


class TestNoDuplicateKeys:
    def test_generated_file_has_each_key_once(self) -> None:
        counts = Counter(_assignments(render_env_example()))
        duplicates = {name: count for name, count in counts.items() if count > 1}

        assert duplicates == {}, f"переменная задана дважды, победит последняя: {duplicates}"

    def test_registry_and_env_only_do_not_collide(self) -> None:
        """Одно и то же имя в двух местах реестра — прямой путь к дублю."""
        registry = {s.env for s in SETTINGS}
        env_only = {name for name, _default, _help in ENV_ONLY}

        assert registry & env_only == set()

    def test_registry_keys_are_unique_by_themselves(self) -> None:
        counts = Counter(s.env for s in SETTINGS)
        duplicates = {name: count for name, count in counts.items() if count > 1}

        assert duplicates == {}

    def test_password_is_declared_exactly_once(self) -> None:
        """Тот самый ключ, с которого всё началось."""
        password_keys = [name for name in _assignments(render_env_example()) if "PASSWORD" in name]

        assert len(password_keys) == len(set(password_keys))
