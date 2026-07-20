"""Keep the MySQL monitor projection synchronized with Prefect's REST API."""

from __future__ import annotations

import logging
import sys
from threading import Event, Thread
from collections.abc import Callable
from typing import Any

from backend.services.monitor_event_service import MonitorEventService
from backend.services.mysql_monitor_store import MySQLMonitorStore
from backend.services.prefect_monitor_adapter import PrefectMonitorAdapter


logger = logging.getLogger(__name__)


def sync_prefect_monitor() -> list[dict[str, Any]]:
    store = MySQLMonitorStore()
    try:
        return PrefectMonitorAdapter(MonitorEventService(store=store)).fetch_and_sync()
    finally:
        store.close()


class MonitorSyncLoop:
    def __init__(
        self,
        *,
        sync: Callable[[], list[dict[str, Any]] | None],
        interval_seconds: int = 300,
        on_runs_changed: Callable[[list[dict[str, Any]]], None] | None = None,
        on_reconciled: Callable[[], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._sync = sync
        self._interval_seconds = interval_seconds
        self._on_runs_changed = on_runs_changed
        self._on_reconciled = on_reconciled
        self._on_error = on_error
        self._stopped = Event()
        self._thread: Thread | None = None

    def run_once(self) -> list[dict[str, Any]]:
        try:
            runs = self._sync() or []
            if self._on_runs_changed and runs:
                self._on_runs_changed(runs)
            if self._on_reconciled:
                self._on_reconciled()
            return runs
        except Exception:
            if self._on_error:
                self._on_error(sys.exception())
            logger.exception("Prefect monitoring sync failed; serving the last successful monitor projection")
            return []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopped.clear()
        self._thread = Thread(target=self._run, name="prefect-monitor-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread:
            self._thread.join(timeout=self._interval_seconds + 1)

    def _run(self) -> None:
        self.run_once()
        while not self._stopped.is_set():
            if self._stopped.wait(self._interval_seconds):
                return
            self.run_once()
