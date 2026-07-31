"""Русские подписи настроек не должны разъезжаться между сервером и клиентом.

Подписи и пояснения настроек по требованию пользователя живут на клиенте, в
словарях локалей. Но реестр на сервере хранит те же тексты по-русски — они
отдаются запасным вариантом, если перевода для настройки ещё не написали.

Отсюда риск, который уже сработал: пояснение к `download_tmp` поправили в
реестре, а клиентский словарь остался с прежней формулировкой — и пользователь
продолжал читать в интерфейсе устаревший текст, потому что клиент выигрывает.

Тест сверяет русские тексты дословно. Английский и японский не проверяем: это
переводы, дословного совпадения там быть не может — но они пишутся с русского,
и если русский верен, переводить будут с верного.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.settings_registry import SETTINGS

RU_LOCALE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "ru.ts"


def _extract(section: str) -> dict[str, str]:
    """Достаёт словарь `section: { key: '…' }` из файла локали.

    Разбор регулярками, а не парсером TypeScript: тащить в тесты node ради
    одного объекта дороже, чем описать его форму. Форма стабильна — файл
    порождается и правится по одному шаблону.
    """
    source = RU_LOCALE.read_text(encoding="utf-8")
    block = re.search(rf"^  {section}: \{{$(.*?)^  \}},$", source, re.MULTILINE | re.DOTALL)
    assert block, f"в ru.ts нет блока {section}"

    found: dict[str, str] = {}
    for line in block.group(1).split("\n"):
        item = re.match(r"^    (\w+): '(.*)',$", line)
        if item:
            # В файле апострофы экранированы, в реестре — нет.
            found[item.group(1)] = item.group(2).replace("\'", "'")
    return found


@pytest.mark.parametrize("setting", SETTINGS, ids=lambda s: s.key)
def test_label_matches_registry(setting) -> None:
    labels = _extract("settingLabel")
    assert setting.key in labels, (
        f"у настройки {setting.key} нет подписи в ru.ts — в интерфейсе она "
        f"покажется серверным текстом, а при смене языка останется русской"
    )
    assert labels[setting.key] == setting.label_ru


@pytest.mark.parametrize("setting", SETTINGS, ids=lambda s: s.key)
def test_help_matches_registry(setting) -> None:
    if not setting.help_ru:
        return
    helps = _extract("settingHelp")
    assert setting.key in helps, f"у настройки {setting.key} нет пояснения в ru.ts"
    assert helps[setting.key] == setting.help_ru, (
        f"пояснение к {setting.key} разошлось: в реестре одно, в ru.ts другое. "
        f"Клиент выигрывает, значит пользователь читает текст из ru.ts"
    )
