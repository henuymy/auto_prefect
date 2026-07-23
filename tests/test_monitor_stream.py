from __future__ import annotations

import asyncio
from datetime import datetime
from time import perf_counter, sleep

from backend.services.monitor_snapshot_service import build_monitor_snapshot, build_run_update
from backend.services.monitor_stream import (
    MonitorRealtimeStatus,
    MonitorStreamHub,
    build_upstream_update,
)


REPORT_RUNS = [
    {
        "id": "completed-report",
        "source": "prefect",
        "external_run_id": "completed-report",
        "task_name": "通报 · 日常进度",
        "target_kind": "report",
        "target_id": "report-deployment-1",
        "trigger": "定时调度",
        "status": "succeeded",
        "started_at": "2026-07-19T09:01:00+08:00",
        "finished_at": "2026-07-19T09:02:00+08:00",
        "current_step": "已完成",
    },
    {
        "id": "stale-cancelled-report",
        "source": "prefect",
        "external_run_id": "stale-cancelled-report",
        "task_name": "通报 · 已清除的历史任务",
        "target_kind": "report",
        "target_id": "report-deployment-stale",
        "trigger": "定时调度",
        "status": "cancelled",
        "scheduled_at": "2026-07-19T09:00:00+08:00",
        "current_step": "已取消",
    },
    {
        "id": "scheduled-report",
        "source": "prefect",
        "external_run_id": "scheduled-report",
        "task_name": "通报 · 明日进度",
        "target_kind": "report",
        "target_id": "report-deployment-2",
        "trigger": "定时调度",
        "status": "scheduled",
        "scheduled_at": "2026-07-20T09:00:00+08:00",
        "current_step": "尚未开始",
    },
]


class RecordingWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.messages: list[dict[str, object]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict[str, object]) -> None:
        self.messages.append(payload)


class BlockingWebSocket:
    async def accept(self) -> None:
        return None

    async def send_json(self, payload: dict[str, object]) -> None:
        await asyncio.sleep(60)


def test_accepted_event_does_not_clear_reconciliation_failure() -> None:
    status = MonitorRealtimeStatus()

    status.record_error("RECONCILIATION_FAILED")
    status.record_accepted()

    assert status.as_dict()["lastErrorCategory"] == "RECONCILIATION_FAILED"


def test_reconciliation_failure_keeps_diagnostics_until_reconciled() -> None:
    status = MonitorRealtimeStatus()

    status.record_error(
        "RECONCILIATION_FAILED",
        RuntimeError("authorization=secret Prefect sync timed out"),
    )
    failed = status.as_dict()

    assert failed["lastErrorAt"] is not None
    assert failed["lastErrorDetail"] == "authorization=[已隐藏] Prefect sync timed out"

    status.record_reconciled()

    assert status.as_dict()["lastErrorCategory"] is None
    assert status.as_dict()["lastErrorAt"] is None
    assert status.as_dict()["lastErrorDetail"] is None


def test_reconciliation_failure_limits_diagnostic_detail() -> None:
    status = MonitorRealtimeStatus()

    status.record_error("RECONCILIATION_FAILED", RuntimeError("x" * 400))

    assert status.as_dict()["lastErrorDetail"] == f"{'x' * 317}..."


def test_upstream_update_contains_the_current_status() -> None:
    status = MonitorRealtimeStatus()
    status.record_error("RECONCILIATION_FAILED", RuntimeError("sync timed out"))

    update = build_upstream_update(status.as_dict())

    assert update["type"] == "upstream.updated"
    assert update["updatedAt"] is not None
    assert update["upstream"]["lastErrorCategory"] == "RECONCILIATION_FAILED"


def test_run_update_excludes_scheduled_history_row_but_refreshes_pending_queue() -> None:
    update = build_run_update(
        REPORT_RUNS,
        "scheduled-report",
        now=datetime.fromisoformat("2026-07-19T10:00:00+08:00"),
    )

    assert update["type"] == "run.updated"
    assert update["runId"] == "scheduled-report"
    assert update["run"] is None
    assert update["pendingQueue"]["total"] == 1


def test_snapshot_excludes_cancelled_stale_runs_from_history() -> None:
    snapshot = build_monitor_snapshot(
        REPORT_RUNS,
        now=datetime.fromisoformat("2026-07-19T10:00:00+08:00"),
    )

    assert [run["id"] for run in snapshot["runs"]] == ["completed-report"]
    assert snapshot["summary"] == {
        "succeeded": 1,
        "running": 0,
        "failed": 0,
        "scheduled": 0,
    }


def test_stream_sends_one_snapshot_then_an_explicit_incremental_update() -> None:
    snapshot = {"type": "snapshot", **build_monitor_snapshot(REPORT_RUNS)}
    update = build_run_update(REPORT_RUNS, "completed-report")
    socket = RecordingWebSocket()
    hub = MonitorStreamHub(load_update=lambda run_id: update if run_id == "completed-report" else None)

    asyncio.run(hub.connect(socket, snapshot))
    asyncio.run(hub.publish_from_async("completed-report"))

    assert socket.accepted is True
    assert [message["type"] for message in socket.messages] == ["snapshot", "run.updated"]
    assert socket.messages[1]["run"]["targetId"] == "report-deployment-1"


def test_stream_sends_an_explicit_upstream_status_update() -> None:
    socket = RecordingWebSocket()
    hub = MonitorStreamHub(load_update=lambda _run_id: None)
    hub._connections.add(socket)
    update = {
        "type": "upstream.updated",
        "updatedAt": "2026-07-23T16:30:00+00:00",
        "upstream": {
            "lastAcceptedAt": "2026-07-23T16:29:00+00:00",
            "lastReconciledAt": "2026-07-23T16:28:00+00:00",
            "lastErrorCategory": "RECONCILIATION_FAILED",
            "lastErrorAt": "2026-07-23T16:30:00+00:00",
            "lastErrorDetail": "Prefect sync timed out",
        },
    }

    asyncio.run(hub.publish_payload_async(update))

    assert socket.messages == [update]


def test_stream_drops_a_blocking_connection_without_delaying_event_delivery() -> None:
    hub = MonitorStreamHub(
        load_update=lambda run_id: {"type": "run.updated", "runId": run_id},
        send_timeout_seconds=0.01,
    )
    socket = BlockingWebSocket()
    hub._connections.add(socket)

    started = perf_counter()
    asyncio.run(hub.publish_from_async("completed-report"))

    assert perf_counter() - started < 0.2
    assert socket not in hub._connections


def test_stream_can_schedule_broadcast_without_waiting_for_it() -> None:
    async def scenario() -> None:
        hub = MonitorStreamHub(
            load_update=lambda run_id: {"type": "run.updated", "runId": run_id},
            send_timeout_seconds=60,
        )
        hub._connections.add(BlockingWebSocket())

        task = hub.publish_background("completed-report")
        assert task.done() is False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_stream_loads_incremental_snapshot_off_the_event_loop() -> None:
    async def scenario() -> None:
        hub = MonitorStreamHub(
            load_update=lambda run_id: (sleep(0.05), {"type": "run.updated", "runId": run_id})[1],
        )
        started = perf_counter()
        task = hub.publish_background("completed-report")
        await asyncio.sleep(0.01)

        assert perf_counter() - started < 0.04
        assert task.done() is False
        await task

    asyncio.run(scenario())
