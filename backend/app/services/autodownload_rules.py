"""Что и для кого качать: сложение глобальной настройки с настройкой тайтла.

Всё здесь считается **отдельно для каждого языка**. Это решение пользователя, и
оно не косметическое: русские субтитры можно хотеть всем подряд, английские —
только там, где включил сам, а японские не качать вовсе. Общего переключателя
«включить автоскачивание» поэтому нет — он был бы четвёртым состоянием поверх
трёх уже имеющихся и означал бы разное для разных языков.

Таблица участия (строки — глобальный режим языка, столбцы — режим тайтла):

|              | default | on  | off |
|--------------|---------|-----|-----|
| `off`        | нет     | нет | нет |
| `configured` | нет     | да  | нет |
| `all`        | да      | да  | нет |

Читается так: `off` выключает язык целиком, и отдельные тайтлы его не включат —
иначе «отключено» означало бы «отключено, кроме». А `off` у тайтла сильнее
любого глобального: явный запрет всегда весомее общего разрешения.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from app.core.db import session_scope
from app.models.autodownload import (
    AUTO_LANGS,
    AutoDownloadRule,
    SeriesAutoDownload,
)


@dataclass(frozen=True)
class Resolved:
    """Действующая настройка для пары «тайтл + язык»."""

    lang: str
    #: Упорядоченный список нормализованных имён групп: выше — приоритетнее.
    priorities: list[str] = field(default_factory=list)
    #: Группы, от которых не качаем никогда. Сильнее любого разрешения.
    blacklist: list[str] = field(default_factory=list)
    #: Из списка ничего не нашлось — взять любую доступную группу.
    allow_any_fallback: bool = False
    #: Список игнорируется целиком: годится любая группа.
    allow_any: bool = False
    #: Качать, даже если субтитры этого языка уже вшиты в контейнер.
    ignore_embedded: bool = False
    #: Откуда взялся список — нужно интерфейсу, чтобы показать «свой» или
    #: «общий», и отчётам, чтобы объяснить решение.
    from_series: bool = False
    #: Когда язык включили. Файлы, лежавшие раньше, автоматически не качаются —
    #: на них есть отдельная кнопка. `None` означает «включили давно, отметки
    #: не осталось»: тогда ограничение по этой границе не применяется.
    enabled_at: datetime | None = None

    @property
    def usable(self) -> bool:
        """Есть ли вообще из чего выбирать.

        Пустой список без разрешения «любая группа» — не настройка, а её
        отсутствие: качать будет нечего, и гонять ради этого запросы к API
        незачем.
        """
        return self.allow_any or bool(self.priorities) or self.allow_any_fallback


def _global_rules() -> dict[str, AutoDownloadRule]:
    with session_scope() as session:
        rows = session.execute(select(AutoDownloadRule)).scalars().all()
        for row in rows:
            session.expunge(row)
    return {row.lang: row for row in rows}


def _series_rules(series_id: int) -> dict[str, SeriesAutoDownload]:
    with session_scope() as session:
        rows = (
            session.execute(
                select(SeriesAutoDownload).where(SeriesAutoDownload.series_id == series_id)
            )
            .scalars()
            .all()
        )
        for row in rows:
            session.expunge(row)
    return {row.lang: row for row in rows}


def _participates(global_mode: str, title_mode: str) -> bool:
    """Таблица участия из шапки модуля, одним местом."""
    if title_mode == "off":
        return False
    if global_mode == "off":
        return False
    if global_mode == "all":
        return True
    # `configured`: качаем только там, где включено явно.
    return title_mode == "on"


def _combine(
    lang: str,
    rule: AutoDownloadRule | None,
    series: SeriesAutoDownload | None,
) -> Resolved | None:
    global_mode = (rule.mode if rule else "off") or "off"
    title_mode = (series.mode if series else "default") or "default"
    if not _participates(global_mode, title_mode):
        return None

    # Пустой список у тайтла означает «взять общий», а не «никого»: именно так
    # работает «удалил свой список — вернулась общая логика».
    own = list(series.priorities or []) if series else []
    priorities = own or (list(rule.priorities or []) if rule else [])

    # Чёрный список наследуется так же: свой перекрывает общий, пустой значит
    # «взять общий». Складывать их не стали — иначе снять унаследованный запрет
    # у отдельного тайтла было бы нечем.
    own_black = list(series.blacklist or []) if series else []
    blacklist = own_black or (list(rule.blacklist or []) if rule else [])

    def inherit(name: str) -> bool:
        # `None` у тайтла — «не задано». Без третьего состояния «не задано»
        # было бы неотличимо от «выключено», и наследовать стало бы нечего.
        if series is not None and getattr(series, name) is not None:
            return bool(getattr(series, name))
        return bool(getattr(rule, name)) if rule else False

    return Resolved(
        lang=lang,
        enabled_at=rule.enabled_at if rule else None,
        priorities=priorities,
        blacklist=blacklist,
        allow_any_fallback=inherit("allow_any_fallback"),
        allow_any=inherit("allow_any"),
        ignore_embedded=inherit("ignore_embedded"),
        from_series=bool(own),
    )


def resolve_for_series(series_id: int) -> dict[str, Resolved]:
    """Языки, для которых этому тайтлу положено качать субтитры.

    Языки, которые не участвуют, в ответе отсутствуют вовсе — вызывающему не
    нужно повторять здешние правила у себя.
    """
    rules = _global_rules()
    series = _series_rules(series_id)

    out: dict[str, Resolved] = {}
    for lang in AUTO_LANGS:
        resolved = _combine(lang, rules.get(lang), series.get(lang))
        if resolved is not None and resolved.usable:
            out[lang] = resolved
    return out


def any_language_enabled() -> bool:
    """Включено ли автоскачивание хоть где-то.

    Дешёвая проверка для планировщика: пока все языки в `off`, обходить
    библиотеку незачем.
    """
    return any((rule.mode or "off") != "off" for rule in _global_rules().values())


def configured_series_ids() -> list[int]:
    """Тайтлы со своей настройкой — для списка «настроено вручную».

    Считается настроенным тот, у кого режим не `default` или есть свой список
    приоритетов: остальные поля без этого ничего не решают.
    """
    with session_scope() as session:
        rows = session.execute(select(SeriesAutoDownload)).scalars().all()
        return sorted(
            {
                row.series_id
                for row in rows
                if (row.mode or "default") != "default" or (row.priorities or [])
            }
        )
