"""Автоскачивание субтитров: каталог групп и настройки приоритетов.

Три таблицы, и разделены они по времени жизни, а не по разделам интерфейса:

* `SubtitleAuthor` — что мы **видели** на Anime365. Наполняется само, по мере
  того как пользователь открывает тайтлы; чистить его незачем.
* `AutoDownloadRule` — что пользователь **хочет**, глобально и на язык.
* `SeriesAutoDownload` — то же самое, но для конкретного тайтла; перекрывает
  глобальное правило.

Настройки не сложены в общий реестр `app_setting` намеренно: там значение —
строка, а здесь у каждого языка свой упорядоченный список групп плюс три
переключателя. Хранить это JSON-строкой в одной ячейке значило бы разбирать её
на каждый чих и потерять возможность спросить «у каких тайтлов свой список».
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

#: Языки, для которых автоскачивание вообще имеет смысл. Совпадает с тем, что
#: умеет остальной сервис: `typeLang` у Anime365 даёт ещё `er` («прочие»),
#: но такие субтитры мы не качаем нигде.
AUTO_LANGS = ("ru", "en", "ja")

#: Режим автоскачивания у тайтла.
#:
#: `default` — как решено глобально для этого языка;
#: `on` — качать, даже если глобально включено только «настроенным тайтлам»;
#: `off` — не качать никогда, даже если глобально включено всем.
TITLE_MODES = ("default", "on", "off")

#: Глобальный режим, свой у каждого языка.
#:
#: `off` — не качать вовсе;
#: `configured` — только тайтлам, где автоскачивание включено явно;
#: `all` — всем тайтлам, кроме тех, где оно явно выключено.
GLOBAL_MODES = ("off", "configured", "all")


class SubtitleAuthor(Base):
    """Группа перевода, встреченная среди субтитров.

    Наполняется лениво: каждый раз, когда мы загружаем переводы тайтла, имена
    авторов оседают здесь. Отдельного обхода каталога не делаем — 29 тысяч
    тайтлов ради списка групп не стоят ни времени, ни запросов к чужому API.

    Ключ — **нормализованное** имя (см. `normalize_author`): без него
    «Crunchyroll», «Crunchyroll [10bit]» и «Crunchyroll (1080)» стали бы тремя
    разными группами, и приоритет пришлось бы задавать каждой.
    """

    __tablename__ = "subtitle_author"
    __table_args__ = (UniqueConstraint("lang", "key", name="uq_subtitle_author"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: Язык субтитров. У каждого языка свои группы: те, кто делает русские
    #: субтитры, к английским отношения обычно не имеют.
    lang: Mapped[str] = mapped_column(String(8), index=True)

    #: Нормализованное имя — по нему сверяются приоритеты.
    key: Mapped[str] = mapped_column(String(128), index=True)

    #: Как показывать человеку. Берётся самая частая исходная форма.
    name: Mapped[str] = mapped_column(String(160))

    #: Сколько переводов этой группы мы видели. Нужен для порядка в подсказке:
    #: сначала те, что встречаются часто, — их и ищут.
    seen: Mapped[int] = mapped_column(Integer, default=0)

    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AutoDownloadRule(Base):
    """Глобальная настройка автоскачивания — своя на каждый язык.

    Строка на язык, а не одна на всё: пользователь может хотеть русские
    субтитры всем подряд, английские — только там, где сам включил, а японские
    не качать вовсе. Общего переключателя «включить автоскачивание» поэтому
    нет — он был бы четвёртым состоянием поверх трёх уже имеющихся.
    """

    __tablename__ = "autodownload_rule"

    lang: Mapped[str] = mapped_column(String(8), primary_key=True)

    #: `off` | `configured` | `all` — см. `GLOBAL_MODES`.
    mode: Mapped[str] = mapped_column(String(16), default="off")

    #: Упорядоченный список нормализованных имён групп: выше — приоритетнее.
    #: Хранится JSON-массивом, потому что это именно список, а не связь: имя
    #: может быть вписано руками и не встречаться в `SubtitleAuthor` вовсе.
    priorities: Mapped[list] = mapped_column(JSON, default=list)

    #: Группы, от которых субтитры не качаем никогда.
    #:
    #: Проверяется РАНЬШЕ списка приоритетов и раньше разрешения «любая
    #: группа»: запрет должен быть сильнее любого разрешения, иначе тумблер
    #: «любая» протащил бы сюда именно то, от чего отписались.
    blacklist: Mapped[list] = mapped_column(JSON, default=list)

    #: Из списка ничего не нашлось — взять любую доступную группу.
    #:
    #: Выключен по умолчанию, и это важно: без него список работает как точный
    #: фильтр, и «никого из списка» означает «субтитры не найдены», а не
    #: «возьмём первого попавшегося».
    allow_any_fallback: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Список не используется вовсе: годится любая группа. Значения списка при
    #: этом сохраняются — переключатель гасит его, а не стирает.
    allow_any: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Качать, даже если субтитры этого языка уже есть вшитыми в контейнер.
    #: Нужен там, где теги дорожек врут (у `Fate HF I` видео помечено `ita`).
    ignore_embedded: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Когда язык последний раз включили.
    #:
    #: Граница между «файл приехал уже при работающем автоскачивании» и «файл
    #: лежал раньше». Требование пользователя: включение галки не должно
    #: задним числом выкачивать субтитры ко всей библиотеке — на это есть
    #: отдельная кнопка. Без отметки времени отличить одно от другого нечем.
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SeriesAutoDownload(Base):
    """Настройка автоскачивания конкретного тайтла — тоже своя на язык.

    Перекрывает глобальную. Пустой `priorities` означает «брать глобальный
    список», а не «никого»: именно так работает «удалили свой список — вернулась
    общая логика».
    """

    __tablename__ = "series_autodownload"
    __table_args__ = (UniqueConstraint("series_id", "lang", name="uq_series_autodownload"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), index=True
    )
    lang: Mapped[str] = mapped_column(String(8), index=True)

    #: `default` | `on` | `off` — см. `TITLE_MODES`.
    mode: Mapped[str] = mapped_column(String(16), default="default")

    priorities: Mapped[list] = mapped_column(JSON, default=list)

    #: Свой чёрный список. Пустой — наследовать общий, как и приоритеты.
    blacklist: Mapped[list] = mapped_column(JSON, default=list)

    #: `None` — наследовать глобальное значение. Именно `None`, а не `False`:
    #: без третьего состояния «не задано» неотличимо от «выключено», и
    #: глобальную настройку было бы нечем унаследовать.
    allow_any_fallback: Mapped[bool | None] = mapped_column(Boolean)
    allow_any: Mapped[bool | None] = mapped_column(Boolean)
    ignore_embedded: Mapped[bool | None] = mapped_column(Boolean)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AutoDownloadMiss(Base):
    """Серия, к которой субтитры искали и не нашли.

    Отдельная очередь, а не флаг у файла: запись переживает и пересканирование
    библиотеки, и правку настроек, и её надо уметь показать человеку списком —
    чтобы он мог выкинуть то, чего заведомо не будет, и не гонять трафик
    впустую.

    «Не нашли» означает именно исчерпанный перебор: среди разрешённых групп
    подходящего перевода нет. Хардсаб сюда тоже относится — скачать его нечем,
    поэтому найденным он не считается.
    """

    __tablename__ = "autodownload_miss"
    __table_args__ = (
        UniqueConstraint("series_id", "episode", "lang", name="uq_autodownload_miss"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), index=True
    )
    episode: Mapped[float] = mapped_column(Float, index=True)
    lang: Mapped[str] = mapped_column(String(8), index=True)

    #: Когда столкнулись впервые — по нему видно, насколько давно ждём.
    first_missed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    #: Сколько раз проверяли. Показывается в списке: десятки попыток — повод
    #: выкинуть запись руками.
    checks: Mapped[int] = mapped_column(Integer, default=1)

    #: Выкинуто человеком: больше не проверяем. Не удаляем строку, а помечаем —
    #: иначе следующий же проход завёл бы её заново.
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
