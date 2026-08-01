"""Пометка «Анонс» у тайтлов, которые ещё не начали выходить.

Признака выхода в API Anime365 нет: `isAiring` не отличает «ещё не началось»
от «давно закончилось». Поэтому он выводится из трёх условий сразу, и каждое
здесь проверяется отдельно — ошибка в любом означает, что чип увидят не те.
"""

from __future__ import annotations

from datetime import datetime

from app.services.coverage import is_announced

YEAR = datetime.utcnow().year


class TestIsAnnounced:
    def test_future_title_without_episodes(self) -> None:
        assert is_announced(YEAR + 1, is_airing=False, content_episodes=0)

    def test_unknown_year_counts_as_future(self) -> None:
        """У анонсов дата часто ещё не объявлена — это ровно наш случай."""
        assert is_announced(None, is_airing=False, content_episodes=0)

    def test_airing_is_not_announced(self) -> None:
        """Онгоинг уже выходит, пусть и не целиком."""
        assert not is_announced(YEAR, is_airing=True, content_episodes=0)

    def test_any_content_episode_cancels_it(self) -> None:
        assert not is_announced(YEAR + 1, is_airing=False, content_episodes=1)

    def test_old_title_without_episodes_is_not_announced(self) -> None:
        """Старый тайтл без серий на сайте — это не анонс, а пробел в каталоге.

        Самый опасный ложный случай: таких тайтлов много, и пометить их
        «анонсом» значит соврать про каждый.
        """
        assert not is_announced(2011, is_airing=False, content_episodes=0)

    def test_current_year_without_episodes_is_announced(self) -> None:
        """Анонс на конец текущего года — обычное дело."""
        assert is_announced(YEAR, is_airing=False, content_episodes=0)
