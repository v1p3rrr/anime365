"""Отдача уже скачанных файлов на просмотр.

Главное здесь — не удобство, а то, что путь берётся из базы по идентификатору.
Принять путь параметром запроса означало бы отдать наружу чтение любого файла
на сервере, и проверка на это стоит первой.
"""

from __future__ import annotations

from pathlib import Path

from app.core.db import session_scope
from app.models.library import LibraryFile, LibraryFolder, LibraryRoot

CONTENT = b"\x00\x01\x02\x03" * 64  # 256 байт — удобно считать границы


def _make_file(tmp_path: Path, name: str = "episode.mp4", kind: str = "video") -> int:
    """Кладёт файл на диск и заводит запись о нём. Возвращает id записи."""
    folder = tmp_path / "Show"
    folder.mkdir(exist_ok=True)
    (folder / name).write_bytes(CONTENT)

    with session_scope() as session:
        root = LibraryRoot(path=str(tmp_path), kind="series", is_available=True)
        session.add(root)
        session.flush()
        library_folder = LibraryFolder(root_id=root.id, rel_path="Show", name="Show")
        session.add(library_folder)
        session.flush()
        file = LibraryFile(folder_id=library_folder.id, filename=name, kind=kind)
        session.add(file)
        session.flush()
        return file.id


class TestPathSafety:
    def test_unknown_id_is_404(self, client, tmp_path) -> None:
        assert client.get("/api/stream/local/99999999").status_code == 404

    def test_non_video_is_refused(self, client, tmp_path) -> None:
        """Субтитры и .nfo через этот эндпоинт не отдаём.

        Он существует ради просмотра; разрешать им же читать всё, что попало в
        библиотеку, — расширение доступа без причины.
        """
        file_id = _make_file(tmp_path, "episode.ru.ass", kind="subtitle")
        assert client.get(f"/api/stream/local/{file_id}").status_code == 404

    def test_missing_file_is_404(self, client, tmp_path) -> None:
        """Файл могли удалить после скана — это не ошибка сервера."""
        file_id = _make_file(tmp_path)
        (tmp_path / "Show" / "episode.mp4").unlink()
        assert client.get(f"/api/stream/local/{file_id}").status_code == 404


class TestRange:
    def test_whole_file(self, client, tmp_path) -> None:
        file_id = _make_file(tmp_path)
        response = client.get(f"/api/stream/local/{file_id}")
        assert response.status_code == 200
        assert response.content == CONTENT
        # Без этого заголовка браузер не даст перемотку вовсе.
        assert response.headers["accept-ranges"] == "bytes"

    def test_partial(self, client, tmp_path) -> None:
        file_id = _make_file(tmp_path)
        response = client.get(
            f"/api/stream/local/{file_id}", headers={"Range": "bytes=10-19"}
        )
        assert response.status_code == 206
        assert response.content == CONTENT[10:20]
        assert response.headers["content-range"] == f"bytes 10-19/{len(CONTENT)}"

    def test_beyond_end_is_416(self, client, tmp_path) -> None:
        file_id = _make_file(tmp_path)
        response = client.get(
            f"/api/stream/local/{file_id}", headers={"Range": "bytes=99999-"}
        )
        assert response.status_code == 416

    def test_media_type_by_extension(self, client, tmp_path) -> None:
        file_id = _make_file(tmp_path, "episode.mkv")
        response = client.get(f"/api/stream/local/{file_id}")
        assert response.headers["content-type"].startswith("video/x-matroska")
