"""Джоба, прерванная перезапуском, не должна висеть «идущей» вечно.

Найдено на живом сервере: скан библиотеки, начатый в 01:59, остался в статусе
`running` после перезапуска контейнера — и индикатор в интерфейсе показывал
бесконечное «идёт сканирование». Ровно та же ловушка, что была у очереди
загрузок (D-37), только там она мешала работать, а здесь врёт о состоянии.
"""

from __future__ import annotations

from app.core.bootstrap import close_orphaned_runs
from app.core.db import session_scope
from app.models.catalog import ScanRun


def _add(kind: str, status: str) -> int:
    with session_scope() as session:
        run = ScanRun(kind=kind, status=status)
        session.add(run)
        session.flush()
        return run.id


class TestOrphanedRuns:
    def test_running_is_closed_as_failed(self) -> None:
        run_id = _add("library", "running")

        assert close_orphaned_runs() >= 1

        with session_scope() as session:
            run = session.get(ScanRun, run_id)
            assert run.status == "failed"
            assert run.finished_at is not None
            assert "перезапуск" in (run.error or "").lower()

    def test_finished_runs_are_left_alone(self) -> None:
        ok_id = _add("catalog_full", "ok")
        close_orphaned_runs()

        with session_scope() as session:
            assert session.get(ScanRun, ok_id).status == "ok"
