from datetime import datetime, timezone

from backend.routers.monitor import _snapshot_payload


def test_snapshot_returns_only_recent_report_executions_and_keeps_all_scheduled_items() -> None:
    runs = [
        {
            "id": "recent-report",
            "task_name": "通报 · 当日经营日报",
            "target_kind": "report",
            "target_id": "report-current",
            "status": "succeeded",
            "source": "prefect",
            "started_at": "2026-07-18T09:00:00+08:00",
            "scheduled_at": "2026-07-18T08:59:00+08:00",
        },
        {
            "id": "old-report",
            "task_name": "通报 · 过期日报",
            "target_kind": "report",
            "target_id": "report-old",
            "status": "failed",
            "source": "prefect",
            "started_at": "2026-06-17T09:00:00+08:00",
        },
        {
            "id": "session",
            "task_name": "session-keeper",
            "target_kind": "system",
            "target_id": "prefect-session",
            "status": "succeeded",
            "source": "prefect",
            "started_at": "2026-07-18T09:00:00+08:00",
        },
        {
            "id": "untimed-report",
            "task_name": "通报 · 缺少时间的失败记录",
            "target_kind": "report",
            "target_id": "report-untimed",
            "status": "failed",
            "source": "prefect",
        },
        {
            "id": "scheduled-report",
            "task_name": "通报 · 明日经营日报",
            "target_kind": "report",
            "target_id": "report-scheduled",
            "status": "scheduled",
            "source": "prefect",
            "scheduled_at": "2026-07-19T09:00:00+08:00",
        },
        {
            "id": "scheduled-dashboard",
            "task_name": "驾驶舱采集 · 指标",
            "target_kind": "system",
            "target_id": "prefect-dashboard",
            "status": "scheduled",
            "source": "prefect",
            "scheduled_at": "2026-07-19T10:00:00+08:00",
        },
    ]

    payload = _snapshot_payload(
        runs,
        now=datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
    )

    assert [item["id"] for item in payload["runs"]] == ["recent-report"]
    assert payload["summary"] == {
        "succeeded": 1,
        "running": 0,
        "failed": 0,
        "scheduled": 0,
    }
    assert [item["id"] for item in payload["pendingQueue"]["items"]] == ["scheduled-report"]


def test_snapshot_exposes_upstream_health_separately_from_websocket_connection() -> None:
    payload = _snapshot_payload(
        [],
        now=datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
        upstream={
            "lastAcceptedAt": "2026-07-18T11:59:58+00:00",
            "lastReconciledAt": "2026-07-18T11:59:57+00:00",
            "lastErrorCategory": "RECONCILIATION_FAILED",
        },
    )

    assert payload["connected"] is True
    assert payload["upstream"]["lastAcceptedAt"] == "2026-07-18T11:59:58+00:00"
    assert payload["upstream"]["lastErrorCategory"] == "RECONCILIATION_FAILED"


def test_prefect_detail_fallback_remains_a_successful_response(monkeypatch) -> None:
    from backend.routers import monitor

    run = {"id": "run-1", "source": "prefect", "status": "failed", "current_step": "运行失败"}
    monkeypatch.setattr(monitor, "prefect_detail_service", type("Details", (), {
        "get_detail": lambda self, item: {**item, "detail_available": False},
    })())

    assert monitor.to_frontend_run(monitor.prefect_detail_service.get_detail(run))["detailAvailable"] is False

def test_scheduled_endpoint_returns_only_report_pending_queue(monkeypatch) -> None:
    from backend.routers import monitor

    monkeypatch.setattr(monitor, "_raw_runs", lambda: [
        {
            "id": "report-scheduled",
            "target_kind": "report",
            "task_name": "通报 · 经营日报",
            "status": "scheduled",
            "scheduled_at": "2026-07-21T09:00:00+08:00",
        },
        {
            "id": "dashboard-scheduled",
            "target_kind": "system",
            "task_name": "驾驶舱采集",
            "status": "scheduled",
            "scheduled_at": "2026-07-21T09:05:00+08:00",
        },
    ])

    payload = monitor.list_scheduled()

    assert payload["total"] == 1
    assert [item["id"] for item in payload["items"]] == ["report-scheduled"]
    assert payload["items"][0]["nextStep"] == "尚未开始"
