from datetime import datetime

from backend.services.monitor_event_service import MemoryMonitorStore, MonitorEventService
from backend.services.prefect_monitor_adapter import PrefectMonitorAdapter


def test_prefect_adapter_uses_deployment_identity_for_report_runs():
    service = MonitorEventService(store=MemoryMonitorStore(), now=lambda: datetime(2026, 7, 18, 9, 0, 0))
    adapter = PrefectMonitorAdapter(service)

    adapter.sync_flow_runs([
        {
            "id": "prefect-report",
            "name": "auburn-rook",
            "deployment_id": "deployment-report",
            "expected_start_time": "2026-07-19T09:00:00Z",
            "state_type": "SCHEDULED",
            "state_name": "Scheduled",
        },
    ],
        deployments_by_id={
            "deployment-report": {
                "id": "deployment-report",
                "name": "notify-经营日报",
                "flow_id": "flow-notify",
            },
        },
        flows_by_id={"flow-notify": {"id": "flow-notify", "name": "auto-notify-flow"}},
    )

    queue = service.list_scheduled()
    assert [item["external_run_id"] for item in queue["items"]] == ["prefect-report"]
    report = service.store.find_by_external_id("prefect", "prefect-report")
    assert report["target_kind"] == "report"
    assert report["target_id"] == "report-deployment-report"
    assert report["task_name"] == "通报 · 经营日报"


def test_prefect_adapter_keeps_failed_state_and_uses_state_message_as_summary():
    service = MonitorEventService(store=MemoryMonitorStore())
    adapter = PrefectMonitorAdapter(service)

    adapter.sync_flow_runs([
        {
            "id": "prefect-crashed-report",
            "deployment_id": "deployment-report",
            "state": {
                "type": "CRASHED",
                "message": "token=secret-value 浏览器会话已中断",
            },
        },
    ],
        deployments_by_id={
            "deployment-report": {
                "id": "deployment-report",
                "name": "notify-经营日报",
                "flow_id": "flow-notify",
            },
        },
        flows_by_id={"flow-notify": {"id": "flow-notify", "name": "auto-notify-flow"}},
    )

    report = service.store.find_by_external_id("prefect", "prefect-crashed-report")

    assert report["status"] == "failed"
    assert report["current_step"] == "运行失败"
    assert report["business_error_summary"] == "token=[已隐藏] 浏览器会话已中断"


def test_prefect_adapter_classifies_prefect_startup_failure_for_business_overview():
    service = MonitorEventService(store=MemoryMonitorStore())
    adapter = PrefectMonitorAdapter(service)

    adapter.sync_flow_runs([
        {
            "id": "prefect-startup-failure",
            "deployment_id": "deployment-report",
            "state": {
                "type": "FAILED",
                "message": "Flow run could not start: unhandled errors in a TaskGroup (1 sub-exception)",
            },
        },
    ],
        deployments_by_id={
            "deployment-report": {
                "id": "deployment-report",
                "name": "notify-经营日报",
                "flow_id": "flow-notify",
            },
        },
        flows_by_id={"flow-notify": {"id": "flow-notify", "name": "auto-notify-flow"}},
    )

    report = service.store.find_by_external_id("prefect", "prefect-startup-failure")

    assert report["current_step"] == "调度初始化"
    assert report["business_error_summary"] == "调度服务未能启动本次通报，未开始执行。"