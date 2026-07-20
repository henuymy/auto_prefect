from __future__ import annotations

from backend.services.monitor_event_service import MemoryMonitorStore
from backend.services.prefect_monitor_event_service import PrefectMonitorEventProcessor


REPORT_FLOW_RUN = {
    "id": "run-1",
    "deployment_id": "deployment-1",
    "expected_start_time": "2026-07-19T09:00:00Z",
    "start_time": "2026-07-19T09:01:00Z",
    "end_time": "2026-07-19T09:02:00Z",
}
REPORT_DEPLOYMENT = {"id": "deployment-1", "name": "notify-日常进度", "flow_id": "flow-1"}
REPORT_FLOW = {"id": "flow-1", "name": "auto-notify-flow"}
SYSTEM_DEPLOYMENT = {"id": "deployment-1", "name": "session-keeper", "flow_id": "flow-1"}
SYSTEM_FLOW = {"id": "flow-1", "name": "session-keeper-flow"}


def prefect_event(*, event_id: str, event_name: str, occurred: str) -> dict[str, object]:
    return {
        "id": event_id,
        "occurred": occurred,
        "event": event_name,
        "resource": {"prefect.resource.id": "prefect.flow-run.run-1"},
        "related": [],
        "payload": {},
    }


def processor(*, deployment: dict[str, str] = REPORT_DEPLOYMENT, flow: dict[str, str] = REPORT_FLOW):
    return PrefectMonitorEventProcessor(
        store=MemoryMonitorStore(),
        fetch_flow_run=lambda flow_run_id: {**REPORT_FLOW_RUN, "id": flow_run_id},
        fetch_deployment=lambda deployment_id: {**deployment, "id": deployment_id},
        fetch_flow=lambda flow_id: {**flow, "id": flow_id},
    )


def test_processor_persists_real_report_identity_from_completed_event() -> None:
    service = processor()

    result = service.process(prefect_event(
        event_id="event-completed",
        event_name="prefect.flow-run.Completed",
        occurred="2026-07-19T09:02:00Z",
    ))

    assert result.accepted is True
    assert result.duplicate is False
    assert result.run is not None
    assert result.run["status"] == "succeeded"
    assert result.run["target_kind"] == "report"
    assert result.run["target_id"] == "report-deployment-1"
    assert result.run["state_occurred_at"] == "2026-07-19T09:02:00+00:00"


def test_processor_deduplicates_repeated_prefect_event() -> None:
    service = processor()
    event = prefect_event(
        event_id="event-completed",
        event_name="prefect.flow-run.Completed",
        occurred="2026-07-19T09:02:00Z",
    )

    first = service.process(event)
    duplicate = service.process(event)

    assert first.accepted is True
    assert duplicate.accepted is False
    assert duplicate.duplicate is True
    assert len(service.store.events[first.run["id"]]) == 1


def test_processor_rejects_older_state_without_overwriting_current_status() -> None:
    service = processor()
    service.process(prefect_event(
        event_id="event-completed",
        event_name="prefect.flow-run.Completed",
        occurred="2026-07-19T09:02:00Z",
    ))

    stale = service.process(prefect_event(
        event_id="event-running-old",
        event_name="prefect.flow-run.Running",
        occurred="2026-07-19T09:01:00Z",
    ))

    stored = service.store.find_by_external_id("prefect", "run-1")
    assert stale.accepted is False
    assert stale.duplicate is True
    assert stored is not None
    assert stored["status"] == "succeeded"


def test_processor_acknowledges_non_report_event_without_persisting_a_run() -> None:
    service = processor(deployment=SYSTEM_DEPLOYMENT, flow=SYSTEM_FLOW)

    result = service.process(prefect_event(
        event_id="event-system",
        event_name="prefect.flow-run.Running",
        occurred="2026-07-19T09:01:00Z",
    ))

    assert result.accepted is False
    assert result.duplicate is False
    assert result.run is None
    assert service.store.list_runs() == []


def test_processor_keeps_failed_state_message_as_summary() -> None:
    failed_run = {
        **REPORT_FLOW_RUN,
        "state": {"type": "FAILED", "message": "token=secret-value 通报发送失败"},
    }
    service = PrefectMonitorEventProcessor(
        store=MemoryMonitorStore(),
        fetch_flow_run=lambda _flow_run_id: failed_run,
        fetch_deployment=lambda _deployment_id: REPORT_DEPLOYMENT,
        fetch_flow=lambda _flow_id: REPORT_FLOW,
    )

    result = service.process(prefect_event(
        event_id="event-failed", event_name="prefect.flow-run.Failed", occurred="2026-07-19T09:02:00Z",
    ))

    assert result.run["business_error_summary"] == "token=[已隐藏] 通报发送失败"


def test_processor_classifies_prefect_startup_failure_for_business_overview() -> None:
    failed_run = {
        **REPORT_FLOW_RUN,
        "state": {
            "type": "FAILED",
            "message": "Flow run could not start: unhandled errors in a TaskGroup (1 sub-exception)",
        },
    }
    service = PrefectMonitorEventProcessor(
        store=MemoryMonitorStore(),
        fetch_flow_run=lambda _flow_run_id: failed_run,
        fetch_deployment=lambda _deployment_id: REPORT_DEPLOYMENT,
        fetch_flow=lambda _flow_id: REPORT_FLOW,
    )

    result = service.process(prefect_event(
        event_id="event-startup-failed", event_name="prefect.flow-run.Failed", occurred="2026-07-19T09:02:00Z",
    ))

    assert result.run is not None
    assert result.run["current_step"] == "调度初始化"
    assert result.run["business_error_summary"] == "调度服务未能启动本次通报，未开始执行。"
    assert result.run["technical_error_summary"] == "Flow run could not start: unhandled errors in a TaskGroup (1 sub-exception)"