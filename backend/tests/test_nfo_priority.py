"""Какой .nfo описывает папку, когда их рядом несколько.

Взято с живой библиотеки: в корне фильмов у 42 папок из 58 лежат сразу
`movie.nfo` и `tvshow.nfo` — след того, что когда-то эти папки сканировались как
сериальная библиотека. Данные в них расходятся у 35 папок из 42, и правильная
сторона всегда `movie.nfo`. Раньше побеждал `tvshow.nfo`, потому что записи
перебирались по алфавиту и он затирал разобранный ранее `movie.nfo`.

Тексты `.nfo` здесь сокращены до значимых тегов, но значения настоящие.
"""

from __future__ import annotations

from pathlib import Path

from app.services.filename_rules import get_rules
from app.services.library_scan import scan_folder

MOVIE_NFO = """<?xml version="1.0" encoding="utf-8"?>
<movie>
  <title>Look Back</title>
  <originaltitle>ルックバック</originaltitle>
  <year>2024</year>
  <anilistid>174788</anilistid>
</movie>
"""

# Тот же настоящий случай: anilistid верный, а название — от другого аниме.
STALE_TVSHOW_NFO = """<?xml version="1.0" encoding="utf-8"?>
<tvshow>
  <title>Kekkai Sensen &amp; Beyond: Zapp Renfro Ingaouhouchuu!!</title>
  <originaltitle>血界戦線 &amp; BEYOND</originaltitle>
  <year>2018</year>
  <anilistid>174788</anilistid>
</tvshow>
"""

SEASON_NFO = """<?xml version="1.0" encoding="utf-8"?>
<season>
  <title>Season 2</title>
  <seasonnumber>2</seasonnumber>
  <premiered>2012-07-02</premiered>
</season>
"""


def _folder(root: Path, name: str, files: dict[str, str]) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    for filename, text in files.items():
        (folder / filename).write_text(text, encoding="utf-8")
    (folder / f"{name} - 01 (BD 1920x1080).mkv").write_bytes(b"x" * 10)
    return folder


class TestNfoPriority:
    def test_movie_root_prefers_movie_nfo(self, tmp_path: Path) -> None:
        folder = _folder(
            tmp_path,
            "Look Back",
            {"movie.nfo": MOVIE_NFO, "tvshow.nfo": STALE_TVSHOW_NFO},
        )

        result = scan_folder(folder, tmp_path, "movie", get_rules())

        assert result.nfo is not None
        assert result.nfo.kind == "movie"
        assert result.nfo.title == "Look Back"
        assert result.nfo.year == 2024

    def test_series_root_prefers_tvshow_nfo(self, tmp_path: Path) -> None:
        """В корне сериалов приоритет обратный.

        Отдельных `movie.nfo` в сериальном корне сейчас нет ни одного, но правило
        должно быть симметричным, а не «фильм всегда главнее».
        """
        folder = _folder(
            tmp_path,
            "Frieren",
            {"movie.nfo": MOVIE_NFO, "tvshow.nfo": STALE_TVSHOW_NFO},
        )

        result = scan_folder(folder, tmp_path, "series", get_rules())

        assert result.nfo is not None
        assert result.nfo.kind == "tvshow"

    def test_season_nfo_wins_inside_season_folder(self, tmp_path: Path) -> None:
        """Сезонная папка описывается своим `season.nfo`, а не унаследованным."""
        folder = _folder(
            tmp_path,
            "Season 2",
            {"season.nfo": SEASON_NFO, "tvshow.nfo": STALE_TVSHOW_NFO},
        )

        result = scan_folder(folder, tmp_path, "series", get_rules())

        assert result.nfo is not None
        assert result.nfo.kind == "season"
        assert result.nfo.premiered == "2012-07-02"

    def test_single_nfo_still_used(self, tmp_path: Path) -> None:
        """Приоритет не должен отбрасывать единственный файл с низким рангом."""
        folder = _folder(tmp_path, "Ghost in the Shell", {"tvshow.nfo": STALE_TVSHOW_NFO})

        result = scan_folder(folder, tmp_path, "movie", get_rules())

        assert result.nfo is not None
        assert result.nfo.kind == "tvshow"
