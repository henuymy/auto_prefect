from __future__ import annotations

from datetime import datetime

from backend.services.monitor_event_service import MemoryMonitorStore, MonitorEventService
from models.monitor import MonitorEvent, MonitorRun


def test_monitor_run_owns_target_identity_columns() -> None:
    columns = MonitorRun.__table__.c

    assert columns.target_kind.type.length == 16
    assert columns.target_id.type.length == 48


def test_monitor_models_store_event_identity_and_state_time() -> None:
    assert MonitorRun.__table__.c.state_occurred_at.nullable is True
    assert MonitorEvent.__table__.c.source_event_id.type.length == 64
    assert MonitorEvent.__table__.c.source_event_id.nullable is True


def test_monitor_event_service_persists_a_sanitized_run_lifecycle() -> None:
    service = MonitorEventService(store=MemoryMonitorStore(), now=lambda: datetime(2026, 7, 17, 9, 0, 0))

    run = service.start_run(
        source="web",
        external_run_id="web-safe-001",
        task_name="配置操作 · 安全测试",
        trigger="网页操作",
    )
    service.update_step(run["id"], name="配置校验", status="running", message="开始校验")
    service.append_log(run["id"], level="INFO", message="token=abc123 https://internal.example/path 已隐藏")
    finished = service.finish_run(run["id"], status="succeeded", current_step="校验完成")

    detail = service.get_run(run["id"])

    assert finished["status"] == "succeeded"
    assert detail["steps"][0]["status"] == "completed"
    assert "abc123" not in detail["logs"][0]["message"]
    assert "internal.example" not in detail["logs"][0]["message"]


def test_scheduled_queue_is_sorted_by_scheduled_time_not_task_name() -> None:
    service = MonitorEventService(store=MemoryMonitorStore(), now=lambda: datetime(2026, 7, 17, 9, 0, 0))
    service.upsert_prefect_run(
        external_run_id="scheduled-later",
        task_name="通报 · Z 任务",
        status="scheduled",
        scheduled_at=datetime(2026, 7, 18, 10, 0, 0),
    )
    service.upsert_prefect_run(
        external_run_id="scheduled-earlier",
        task_name="通报 · A 任务",
        status="scheduled",
        scheduled_at=datetime(2026, 7, 18, 8, 0, 0),
    )

    queue = service.list_scheduled()

    assert queue["total"] == 2
    assert [item["external_run_id"] for item in queue["items"]] == ["scheduled-earlier", "scheduled-later"]
