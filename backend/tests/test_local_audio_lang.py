"""Определение языка звука у локального видео.

От этого значения зависит, какие разделы панели загрузки серии будут спрятаны
как «уже есть». Цена ошибки несимметрична: лишний показанный раздел — мелкое
неудобство, а спрятанный нужный означает, что пользователь не увидел того, чего
ему как раз не хватает. Поэтому отдельный тест на то, что «не знаем» остаётся
«не знаем» и не превращается в догадку.
"""

from __future__ import annotations

from app.services.title_service import _local_audio_langs


class TestSourcePriority:
    """Три источника по убыванию надёжности: наш перевод, .nfo, суффикс имени."""

    def test_our_own_download_wins_over_everything(self):
        # Файл скачан нами, и язык известен точно. Даже если .nfo рядом врёт
        # (у Fate HF в реальной библиотеке у видеодорожки стоит `ita`),
        # верить надо переводу, а не метаданным Jellyfin.
        item = {
            "translation_id": 42,
            "streams": {"audio_languages": ["ita"]},
            "lang": "en",
        }
        assert _local_audio_langs(item, {42: "ru"}) == {"ru"}

    def test_nfo_used_when_translation_unknown(self):
        item = {"translation_id": None, "streams": {"audio_languages": ["jpn"]}, "lang": None}
        assert _local_audio_langs(item, {}) == {"ja"}

    def test_filename_suffix_is_the_last_resort(self):
        item = {"translation_id": None, "streams": None, "lang": "ru"}
        assert _local_audio_langs(item, {}) == {"ru"}

    def test_unknown_translation_id_falls_through_to_nfo(self):
        # Перевода нет в выдаче API (снят с публикации) — молча уходим к .nfo,
        # а не считаем, что языка нет.
        item = {"translation_id": 9, "streams": {"audio_languages": ["rus"]}, "lang": None}
        assert _local_audio_langs(item, {}) == {"ru"}


class TestUnknownStaysUnknown:
    def test_no_data_at_all(self):
        item = {"translation_id": None, "streams": None, "lang": None}
        assert _local_audio_langs(item, {}) == set()

    def test_language_beyond_the_usual_three_is_now_recognised(self):
        # Раньше здесь ожидалось пустое множество: приведение языков умело
        # только ru/en/ja и всё остальное молча выбрасывало. После перехода на
        # ISO-справочник итальянская дорожка распознаётся — это и было целью,
        # а не побочный эффект.
        item = {"translation_id": None, "streams": {"audio_languages": ["ita"]}, "lang": None}
        assert _local_audio_langs(item, {}) == {"it"}

    def test_garbage_is_still_not_a_guess(self):
        # А вот выдумывать по-прежнему не надо: нераспознанное даёт пустое
        # множество, то есть «не знаем», и панель ничего не прячет.
        item = {"translation_id": None, "streams": {"audio_languages": ["не язык"]}, "lang": None}
        assert _local_audio_langs(item, {}) == set()

    def test_translation_with_other_language_does_not_leak(self):
        # У перевода `typeLang` бывает `er` (прочие языки) — такой в набор
        # не попадает, и мы уходим к следующему источнику.
        item = {"translation_id": 42, "streams": {"audio_languages": ["jpn"]}, "lang": None}
        assert _local_audio_langs(item, {42: "er"}) == {"ja"}


class TestMultipleTracks:
    def test_dual_audio_reports_both(self):
        # Двухдорожечные раздачи обычны: японский плюс озвучка. Обе дорожки
        # должны попасть в ответ, иначе панель предложит скачать то, что есть.
        item = {
            "translation_id": None,
            "streams": {"audio_languages": ["jpn", "rus"]},
            "lang": None,
        }
        assert _local_audio_langs(item, {}) == {"ja", "ru"}

    def test_duplicate_codes_collapse(self):
        item = {
            "translation_id": None,
            "streams": {"audio_languages": ["jpn", "ja", "jap"]},
            "lang": None,
        }
        assert _local_audio_langs(item, {}) == {"ja"}
