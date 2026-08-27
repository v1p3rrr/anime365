"""Досбор не возвращается к идентификатору, который не нашёлся.

⚠️ Заведено по живой поломке. В логе сервера каждые две минуты стояло:

    Досбор переводов: неизвестных идентификаторов 1
    Досбор переводов: тайтлов найдено 1, загружено 1

И так сутками, всегда про один и тот же `t5286141` из файла
`RU [Anime365] Bocchi the Rock Movie - 02 (t5286141).ass`. Выглядело как успех:
тайтл действительно находился и действительно грузился. Но самого перевода в его
списке не было — удалён с сайта, — поэтому идентификатор оставался неизвестным,
и следующий скан начинал всё заново. Сходимости не было никогда.

Отсюда правило: считаем итог по факту (появился ли перевод в кэше), а не по
успеху загрузки тайтла, и к ненайденному возвращаемся не чаще раза в сутки.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.catalog import Series, Translation, TranslationMiss
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot
from app.services import translation_backfill
from app.services.translation_backfill import (
    ERROR_RETRY_AFTER,
    RETRY_AFTER,
    backfill,
    remember_outcome,
    unknown_translation_ids,
)

#: Тот самый перевод, удалённый с сайта.
GONE_ID = 5_286_141
SERIES_ID = 950_101


@pytest.fixture(autouse=True)
def _clean():
    """Чистим по содержимому: файл БД переживает прогоны."""
    _wipe()
    yield
    _wipe()


def _wipe() -> None:
    with session_scope() as session:
        session.execute(delete(TranslationMiss))
        session.execute(delete(Translation).where(Translation.id == GONE_ID))
        session.execute(
            delete(LibraryRoot).where(LibraryRoot.path == "/tmp/a365-backfill-misses")
        )


def _library_file_with(translation_id: int) -> None:
    """Файл, в имени которого стоит `t{id}` — вход для досбора."""
    with session_scope() as session:
        if session.get(Series, SERIES_ID) is None:
            session.add(Series(id=SERIES_ID, title="Bocchi the Rock! Movie", type="movie"))
        root = LibraryRoot(path="/tmp/a365-backfill-misses", kind="series")
        session.add(root)
        session.flush()
        folder = LibraryFolder(root_id=root.id, rel_path="Bocchi", name="Bocchi")
        session.add(folder)
        session.flush()
        session.add(
            LibraryFile(
                folder_id=folder.id,
                filename=f"RU [Anime365] Bocchi the Rock Movie - 02 (t{translation_id}).ass",
                kind="subtitle",
                anime365_translation_id=translation_id,
            )
        )


class TestWhatGetsAsked:
    def test_an_unknown_id_is_asked_about(self) -> None:
        _library_file_with(GONE_ID)
        assert GONE_ID in unknown_translation_ids()

    def test_a_fresh_miss_is_not_asked_again(self) -> None:
        _library_file_with(GONE_ID)
        remember_outcome([GONE_ID], {})

        assert GONE_ID not in unknown_translation_ids(), "спросили сегодня — хватит"

    def test_a_stale_miss_is_asked_again(self) -> None:
        """Сутки прошли — перевод мог вернуться, спрашиваем ещё раз."""
        _library_file_with(GONE_ID)
        remember_outcome([GONE_ID], {})

        with session_scope() as session:
            row = session.get(TranslationMiss, GONE_ID)
            row.last_try_at = datetime.utcnow() - RETRY_AFTER - timedelta(minutes=1)

        assert GONE_ID in unknown_translation_ids()


class TestHowTheOutcomeIsJudged:
    def test_loaded_title_without_the_translation_counts_as_a_miss(self) -> None:
        """Тот самый случай: тайтл загрузился, а перевода в нём нет."""
        _library_file_with(GONE_ID)

        assert remember_outcome([GONE_ID], {}) == 1
        with session_scope() as session:
            assert session.get(TranslationMiss, GONE_ID).reason == "not_in_series"

    def test_a_translation_that_arrived_leaves_no_trace(self) -> None:
        _library_file_with(GONE_ID)
        with session_scope() as session:
            session.add(Translation(id=GONE_ID, series_id=SERIES_ID))

        assert remember_outcome([GONE_ID], {}) == 0
        with session_scope() as session:
            assert session.get(TranslationMiss, GONE_ID) is None

    def test_an_earlier_miss_is_forgotten_once_it_arrives(self) -> None:
        """Перевод вернулся на сайт — прошлые неудачи больше ничего не значат."""
        _library_file_with(GONE_ID)
        remember_outcome([GONE_ID], {})

        with session_scope() as session:
            session.add(Translation(id=GONE_ID, series_id=SERIES_ID))
        remember_outcome([GONE_ID], {})

        with session_scope() as session:
            assert session.get(TranslationMiss, GONE_ID) is None

    def test_a_network_refusal_is_marked_as_such(self) -> None:
        """Отказ сети — временный, и в таблице он должен отличаться от удаления."""
        _library_file_with(GONE_ID)
        remember_outcome([GONE_ID], {GONE_ID: "error"})

        with session_scope() as session:
            assert session.get(TranslationMiss, GONE_ID).reason == "error"

    def test_attempts_accumulate(self) -> None:
        _library_file_with(GONE_ID)
        remember_outcome([GONE_ID], {})
        remember_outcome([GONE_ID], {})

        with session_scope() as session:
            assert session.get(TranslationMiss, GONE_ID).attempts == 2


class TestTheLoopIsClosed:
    def test_second_pass_does_not_go_to_the_network(self, monkeypatch) -> None:
        """Главное: второй проход подряд не должен спрашивать сайт ни о чём."""
        _library_file_with(GONE_ID)
        asked: list[list[int]] = []

        async def resolve(ids):
            asked.append(list(ids))
            # Сайт отвечает успешно и называет тайтл — как оно и было.
            return {ids[0]: SERIES_ID}, {}

        async def load(series_ids):
            # Тайтл грузится, но нужного перевода в нём нет.
            return set(series_ids)

        monkeypatch.setattr(translation_backfill, "resolve_series", resolve)
        monkeypatch.setattr(translation_backfill, "load_series", load)

        first = asyncio.run(backfill())
        second = asyncio.run(backfill())

        assert first["unknown"] == 1
        assert first["hopeless"] == 1
        assert second["unknown"] == 0, "во втором проходе спрашивать уже нечего"
        assert len(asked) == 1, "в сеть сходили ровно один раз"

    def test_the_miss_table_stays_small(self) -> None:
        """Остатки — это остатки: строк столько же, сколько неудачных id."""
        _library_file_with(GONE_ID)
        remember_outcome([GONE_ID], {})
        remember_outcome([GONE_ID], {})

        with session_scope() as session:
            assert len(session.execute(select(TranslationMiss.translation_id)).all()) == 1
class TestNetworkRefusalComesBackSooner:
    """Отказ сети — случайность, а «перевода нет» — состояние.

    ⚠️ Различие было записано в трёх местах — в комментарии модели, в докстроке
    `resolve_series` и в [D-156](../../claude/09-decisions.md), — и ни в одном
    не исполнялось: отбор смотрел только на дату попытки, а поле `reason`
    писалось и **не читалось ни разу**. Разовый обрыв связи откладывал
    идентификатор на сутки наравне с переводом, удалённым с сайта.
    """

    @staticmethod
    def _age(translation_id: int, delta) -> None:
        with session_scope() as session:
            session.get(TranslationMiss, translation_id).last_try_at = (
                datetime.utcnow() - delta
            )

    def test_a_network_refusal_is_retried_after_its_own_window(self) -> None:
        _library_file_with(GONE_ID)
        remember_outcome([GONE_ID], {GONE_ID: "error"})
        self._age(GONE_ID, ERROR_RETRY_AFTER + timedelta(minutes=1))

        assert GONE_ID in unknown_translation_ids(), "сетевой отказ временный"

    def test_a_missing_translation_still_waits_a_day(self) -> None:
        # Тот же возраст записи, но причина другая — и ждать ей сутки.
        _library_file_with(GONE_ID)
        remember_outcome([GONE_ID], {})
        self._age(GONE_ID, ERROR_RETRY_AFTER + timedelta(minutes=1))

        assert GONE_ID not in unknown_translation_ids(), "перевода нет — не дёргаем"

    def test_a_fresh_network_refusal_is_not_retried(self) -> None:
        # Обратная сторона: короткое окно не означает «каждый скан».
        _library_file_with(GONE_ID)
        remember_outcome([GONE_ID], {GONE_ID: "error"})
        self._age(GONE_ID, ERROR_RETRY_AFTER - timedelta(minutes=1))

        assert GONE_ID not in unknown_translation_ids()

    def test_an_unknown_reason_waits_a_day(self) -> None:
        """Быстрое окно даётся только явному `error`.

        ⚠️ Сперва здесь проверялась причина `None` — и тест справедливо упал:
        колонка объявлена непустой со значением по умолчанию, то есть `NULL` в
        ней быть не может. Настоящий пограничный случай — пустая строка.

        Всё, что не `error`, идёт по суточному окну: иначе правка вернула бы
        ровно тот вечный поход в сеть, ради которого таблица и заведена.
        """
        _library_file_with(GONE_ID)
        remember_outcome([GONE_ID], {})
        with session_scope() as session:
            row = session.get(TranslationMiss, GONE_ID)
            row.reason = ""
            row.last_try_at = datetime.utcnow() - ERROR_RETRY_AFTER - timedelta(minutes=1)

        assert GONE_ID not in unknown_translation_ids()

    def test_after_a_full_day_even_a_missing_one_is_asked(self) -> None:
        # И прежнее правило на месте: сутки прошли — спрашиваем снова.
        _library_file_with(GONE_ID)
        remember_outcome([GONE_ID], {})
        self._age(GONE_ID, RETRY_AFTER + timedelta(minutes=1))

        assert GONE_ID in unknown_translation_ids()

