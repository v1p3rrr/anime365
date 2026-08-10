"""Каталог групп перевода: одна команда — одна запись.

`authorsSummary` приходит с сайта как есть, и одна и та же группа встречается в
нескольких написаниях. Если их не свести, в списке приоритетов придётся
перечислять все варианты, а промахнувшись мимо одного — молча не скачать
субтитры. Ровно это и проверяется ниже.

Обратная ошибка опаснее и потому проверяется отдельно: склеить две разные
команды в одну незаметно, а лишняя строка в подсказке видна сразу.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from app.core.db import session_scope
from app.models.autodownload import SubtitleAuthor
from app.services.subtitle_authors import (
    display_name,
    entry_matches,
    normalize_author,
    remember_authors,
    suggest_authors,
)


def _translation(author: str, lang: str = "ru", kind: str = "sub") -> dict:
    return {"authors_summary": author, "type_lang": lang, "type_kind": kind}


def _wipe(*keys: str) -> None:
    with session_scope() as session:
        session.execute(delete(SubtitleAuthor).where(SubtitleAuthor.key.in_(keys)))


class TestOneTeamOneKey:
    """Технический хвост не должен плодить двойников."""

    def test_brackets_and_parentheses_fall_off(self) -> None:
        assert normalize_author("Crunchyroll [10bit]") == normalize_author("Crunchyroll")
        assert normalize_author("Crunchyroll (1080p)") == normalize_author("Crunchyroll")

    def test_trailing_quality_falls_off_without_brackets(self) -> None:
        """«SubsPlease 1080p» — та же команда, просто без скобок."""
        assert normalize_author("SubsPlease 1080p") == normalize_author("SubsPlease")

    def test_case_and_separators_do_not_matter(self) -> None:
        assert normalize_author("Anime-Zone") == normalize_author("anime zone")
        assert normalize_author("AniLibria") == normalize_author("anilibria")

    def test_a_long_technical_tail_falls_off_whole(self) -> None:
        assert normalize_author("Wakanim [BDRip 1080p x265]") == normalize_author("Wakanim")


class TestDifferentTeamsStayApart:
    """Ошибка в эту сторону не видна глазом, поэтому проверяется явно."""

    def test_a_named_suffix_is_part_of_the_name(self) -> None:
        """`(Aero)` — это команда, а не описание файла."""
        assert normalize_author("YakuSub Studio (Aero)") != normalize_author("YakuSub Studio")

    def test_a_half_technical_group_is_left_alone(self) -> None:
        """Выкусывать часть скобки значило бы гадать, что там имя."""
        assert normalize_author("Kazoku (Aero 1080p)") != normalize_author("Kazoku")

    def test_similar_names_are_not_merged(self) -> None:
        assert normalize_author("AniPlague") != normalize_author("AniPlay")


class TestNotAnAuthorAtAll:
    """Сайт иногда кладёт в это поле описание дорожки, а не команду."""

    def test_a_codec_line_yields_no_key(self) -> None:
        # Тот самый случай из живых данных: `EAC-3 2.0`.
        assert normalize_author("EAC-3 2.0") == ""

    def test_empty_input_yields_no_key(self) -> None:
        assert normalize_author(None) == ""
        assert normalize_author("   ") == ""

    def test_such_rows_do_not_reach_the_catalogue(self) -> None:
        assert remember_authors([_translation("EAC-3 2.0")]) == 0


class TestDisplayName:
    def test_the_shown_name_keeps_its_case(self) -> None:
        assert display_name("Crunchyroll [10bit]") == "Crunchyroll"

    def test_the_shown_name_loses_the_tail_too(self) -> None:
        """Иначе в списке рядом стоят «SubsPlease» и «SubsPlease 1080p»."""
        assert display_name("SubsPlease 1080p") == "SubsPlease"


class TestRemembering:
    def test_only_subtitles_are_remembered(self) -> None:
        """Озвучка к выбору субтитров отношения не имеет."""
        _wipe("озвучка тест")
        remember_authors([_translation("Озвучка Тест", kind="voice")])

        with session_scope() as session:
            found = session.execute(
                select(SubtitleAuthor).where(SubtitleAuthor.key == "озвучка тест")
            ).scalar_one_or_none()

        assert found is None

    def test_unsupported_language_is_skipped(self) -> None:
        """`er` — «прочие языки», мы их не качаем нигде."""
        assert remember_authors([_translation("Кто-то", lang="er")]) == 0

    def test_variants_collapse_into_one_row(self) -> None:
        """Главное свойство каталога, ради которого он и нормализуется."""
        _wipe("группа вариантов")
        remember_authors(
            [
                _translation("Группа Вариантов"),
                _translation("Группа-Вариантов [1080p]"),
                _translation("группа вариантов (BDRip)"),
            ]
        )

        with session_scope() as session:
            rows = (
                session.execute(
                    select(SubtitleAuthor).where(SubtitleAuthor.key == "группа вариантов")
                )
                .scalars()
                .all()
            )

        assert len(rows) == 1
        assert rows[0].seen == 3

    def test_the_same_team_in_two_languages_is_two_rows(self) -> None:
        """Русские и английские субтитры одной команды выбираются отдельно."""
        _wipe("двуязычные")
        remember_authors([_translation("Двуязычные", lang="ru")])
        remember_authors([_translation("Двуязычные", lang="en")])

        with session_scope() as session:
            rows = (
                session.execute(
                    select(SubtitleAuthor).where(SubtitleAuthor.key == "двуязычные")
                )
                .scalars()
                .all()
            )

        assert {row.lang for row in rows} == {"ru", "en"}

    def test_reloading_a_title_does_not_inflate_the_counter(self) -> None:
        """Переводы грузятся целиком и повторно — счётчик не должен расти."""
        _wipe("постоянная группа")
        rows = [_translation("Постоянная Группа"), _translation("Постоянная Группа")]
        remember_authors(rows)
        remember_authors(rows)

        with session_scope() as session:
            found = session.execute(
                select(SubtitleAuthor).where(SubtitleAuthor.key == "постоянная группа")
            ).scalar_one()

        assert found.seen == 2


class TestSuggestions:
    def test_filter_matches_the_normalized_form(self) -> None:
        """Ищут как придётся: «Кранч», «кранч», «КРАНЧ» — всё это одно."""
        _wipe("кранчироллтест")
        remember_authors([_translation("КранчироллТест")])

        found = [row["key"] for row in suggest_authors("ru", query="кранчиролл")]

        assert "кранчироллтест" in found

    def test_another_language_does_not_leak_in(self) -> None:
        _wipe("только английская")
        remember_authors([_translation("Только Английская", lang="en")])

        assert "только английская" not in [row["key"] for row in suggest_authors("ru")]

    def test_frequent_teams_come_first(self) -> None:
        """Кого видели чаще — того и ищут."""
        _wipe("редкая группа", "частая группа")
        remember_authors([_translation("Редкая Группа")])
        remember_authors([_translation("Частая Группа") for _ in range(9)])

        order = [row["key"] for row in suggest_authors("ru")]

        assert order.index("частая группа") < order.index("редкая группа")


class TestRegexEntries:
    """Запись списка может быть шаблоном, а не именем группы.

    Просьба пользователя. Имя удобно, пока группа пишется одинаково; шаблон
    нужен там, где написаний много и заранее их не перечислить.
    """

    def test_a_plain_entry_matches_the_normalized_key(self) -> None:
        assert entry_matches("crunchyroll", "Crunchyroll [10bit]", "crunchyroll")

    def test_a_plain_entry_does_not_match_a_stranger(self) -> None:
        assert not entry_matches("wakanim", "Crunchyroll", "crunchyroll")

    def test_a_pattern_matches_the_normalized_form(self) -> None:
        assert entry_matches("re:^crunchy", "Crunchyroll [10bit]", "crunchyroll")

    def test_a_pattern_can_reach_what_normalization_drops(self) -> None:
        """Ради этого шаблон и сверяется с исходным написанием тоже."""
        assert entry_matches(r"re:\[10bit\]", "Crunchyroll [10bit]", "crunchyroll")

    def test_a_pattern_is_case_insensitive(self) -> None:
        assert entry_matches("re:CRUNCHY", "Crunchyroll", "crunchyroll")

    def test_a_broken_pattern_matches_nothing(self) -> None:
        """Битый шаблон не должен ронять подбор — он просто ни с чем не сходится."""
        assert not entry_matches("re:[", "Crunchyroll", "crunchyroll")


class TestBackfill:
    """Каталог дособирается из переводов, сохранённых до его появления.

    Живой случай: у пользователя страница тайтла показывала перевод от
    Crunchyroll, а подсказка оставалась пустой. Переводы кэшируются надолго,
    повторный заход берёт их из базы, не обращаясь к API, — а наполнение
    каталога висело только на пути загрузки.
    """

    def test_stored_translations_reach_the_catalogue(self) -> None:
        from app.models.catalog import Series, Translation
        from app.services.subtitle_authors import backfill_from_stored_translations

        _wipe("группа из базы")
        with session_scope() as session:
            session.merge(Series(id=930_001, title="Backfill", type="tv"))
            session.merge(
                Translation(
                    id=930_101,
                    series_id=930_001,
                    type_kind="sub",
                    type_lang="ru",
                    authors_summary="Группа Из Базы",
                )
            )

        backfill_from_stored_translations()

        assert "группа из базы" in [row["key"] for row in suggest_authors("ru")]
