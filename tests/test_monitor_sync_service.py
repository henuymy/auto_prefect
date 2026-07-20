from backend.services.monitor_sync_service import MonitorSyncLoop
from threading import Thread
from time import sleep


def test_monitor_sync_loop_runs_the_injected_sync_once() -> None:
    calls: list[str] = []
    loop = MonitorSyncLoop(sync=lambda: calls.append("sync"), interval_seconds=15)

    loop.run_once()

    assert calls == ["sync"]


def test_monitor_sync_loop_defaults_to_five_minute_reconciliation() -> None:
    assert MonitorSyncLoop(sync=lambda: None)._interval_seconds == 300


def test_monitor_sync_loop_reconciles_immediately_when_started() -> None:
    calls: list[str] = []
    loop = MonitorSyncLoop(sync=lambda: calls.append("sync"), interval_seconds=60)
    thread = Thread(target=loop._run, daemon=True)

    thread.start()
    sleep(0.05)
    loop.stop()

    assert calls == ["sync"]


def test_monitor_sync_loop_reports_reconciliation_errors() -> None:
    errors: list[Exception] = []

    def fail() -> None:
        raise OSError("Prefect is unavailable")

    loop = MonitorSyncLoop(sync=fail, on_error=errors.append)

    assert loop.run_once() == []
    assert len(errors) == 1
    assert str(errors[0]) == "Prefect is unavailable"
