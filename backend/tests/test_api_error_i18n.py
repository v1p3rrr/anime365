"""У каждого кода ошибки должен быть перевод во всех трёх словарях.

Механизм из [D-48](../../claude/09-decisions.md) устроен так: сервер отдаёт
машинный код, а текст собирает клиент по своему словарю. Русское сообщение
кладётся в ответ запасным вариантом — для логов, `curl` и незнакомого кода.

⚠️ **Запасной вариант и оказался дырой.** Коды добавляли, словари не трогали, и
незнакомый код показывался русским текстом — в английском и японском интерфейсе
тоже. Молча: ошибки нет, место не пустое, просто чужой язык. На ревизии таких
кодов нашлось **тринадцать из сорока пяти**, включая частые
(`paths_overlap` при сохранении настроек, `user_exists` при заведении учётной
записи, `jellyfin_*` у кнопок коллекций).

Тест сторожит именно стык: список кодов на сервере против ключей `apiErrors` в
`ru.ts`, `en.ts` и `ja.ts`. Тексты не сверяет — они переводы, дословно совпадать
не должны; сверяет **наличие ключа** и **набор подстановок**: потерянный при
переводе `{reason}` оставляет пользователя без причины отказа.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core import errors

LOCALES = ("ru", "en", "ja")
I18N_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"

#: Коды, которых на сервере нет и быть не может.
#:
#: `network` придумывает сам клиент, когда запрос до сервера не дошёл: кода
#: взять неоткуда, а объяснить надо — и на языке интерфейса. Лежит он в том же
#: блоке `apiErrors` намеренно, чтобы перевод искался одним способом.
CLIENT_SIDE_CODES = {"network"}


def _server_codes() -> set[str]:
    """Коды из `core/errors.py` — по константам модуля, а не по регуляркам.

    Константа кода: имя заглавными, значение строкой в нижнем регистре. Так
    новый код попадает под проверку сам, без правки теста.
    """
    return {
        value
        for name, value in vars(errors).items()
        if name.isupper() and isinstance(value, str) and value.islower()
    }


def _dictionary(locale: str) -> dict[str, str]:
    """Блок `apiErrors` из словаря локали.

    Разбор регулярками, а не парсером TypeScript: тащить node в тесты дороже,
    чем описать форму файла, а форма стабильна — так же устроен
    `test_settings_i18n_sync.py`.
    """
    source = (I18N_DIR / f"{locale}.ts").read_text(encoding="utf-8")
    block = re.search(r"^  apiErrors: \{$(.*?)^  \},$", source, re.MULTILINE | re.DOTALL)
    assert block, f"в {locale}.ts нет блока apiErrors"
    # Длинную строку принято переносить на следующую строку — склеиваем, иначе
    # такой ключ выпал бы из проверки молча, из-за одного лишь форматирования.
    text = re.sub(r":\n\s+'", ": '", block.group(1))
    return dict(re.findall(r"^    (\w+): '(.*)',$", text, re.MULTILINE))


@pytest.mark.parametrize("locale", LOCALES)
def test_every_code_has_a_translation(locale: str) -> None:
    missing = sorted(_server_codes() - set(_dictionary(locale)))
    assert not missing, (
        f"в {locale}.ts нет ключей apiErrors для кодов: {', '.join(missing)}. "
        f"Без них пользователь увидит русский текст сервера — ошибки не будет, "
        f"будет чужой язык"
    )


@pytest.mark.parametrize("locale", LOCALES)
def test_no_translations_for_codes_that_are_gone(locale: str) -> None:
    extra = sorted(set(_dictionary(locale)) - _server_codes() - CLIENT_SIDE_CODES)
    assert not extra, (
        f"в {locale}.ts остались ключи apiErrors без кода на сервере: "
        f"{', '.join(extra)}. Показать их нечем — это мёртвые строки"
    )


def test_placeholders_match_across_locales() -> None:
    """Перевод не должен терять подстановку.

    Потерянный `{reason}` не ломает ничего видимого: строка покажется как есть,
    без причины отказа. Заметить это можно только на нужном языке и нужной
    ошибке разом, поэтому сторожим механически.
    """
    dictionaries = {locale: _dictionary(locale) for locale in LOCALES}
    for key, text in dictionaries["ru"].items():
        expected = set(re.findall(r"\{(\w+)\}", text))
        for locale in ("en", "ja"):
            found = set(re.findall(r"\{(\w+)\}", dictionaries[locale].get(key, "")))
            assert found == expected, (
                f"{locale}.ts, apiErrors.{key}: подстановки разошлись с русским "
                f"({sorted(found)} против {sorted(expected)})"
            )
