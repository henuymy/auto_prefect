from __future__ import annotations

from typing import Any

from backend.services.prefect_monitor_detail_service import PrefectMonitorDetailService


def test_prefect_detail_service_returns_sanitized_real_steps_logs_and_failure_summary() -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        calls.append((method, path, payload))
        if path == "flow_runs/prefect-run-1":
            return {
                "id": "prefect-run-1",
                "state": {"type": "FAILED", "message": "password=secret-value 上游报表下载失败"},
                "start_time": "2026-07-20T01:00:00Z",
                "end_time": "2026-07-20T01:02:00Z",
            }
        if path == "task_runs/filter":
            return [
                {
                    "name": "会话检查",
                    "state": {"type": "COMPLETED", "message": "会话有效"},
                    "start_time": "2026-07-20T01:00:00Z",
                    "end_time": "2026-07-20T01:00:10Z",
                },
                {
                    "name": "发送通报",
                    "state": {"type": "FAILED", "message": "token=task-secret 发送失败"},
                    "start_time": "2026-07-20T01:00:11Z",
                    "end_time": "2026-07-20T01:02:00Z",
                },
            ]
        if path == "logs/filter":
            return [
                {
                    "timestamp": "2026-07-20T01:01:59Z",
                    "level": 40,
                    "message": r"C:\Users\yuyu\Desktop\项目\自动通报\flows\notify.py token=log-secret 发送异常",
                },
            ]
        raise AssertionError(f"unexpected request: {method} {path}")

    detail = PrefectMonitorDetailService(request=request).get_detail({
        "id": "monitor-run-1",
        "source": "prefect",
        "external_run_id": "prefect-run-1",
        "status": "failed",
        "current_step": "运行失败",
        "business_error_summary": "已同步摘要",
    })

    assert [path for _, path, _ in calls] == [
        "flow_runs/prefect-run-1",
        "task_runs/filter",
        "logs/filter",
    ]
    assert detail["detail_available"] is True
    assert detail["current_step"] == "发送通报"
    assert detail["business_error_summary"] == "password=[已隐藏] 上游报表下载失败"
    assert detail["steps"][0]["status"] == "completed"
    assert detail["steps"][1]["status"] == "failed"
    assert detail["steps"][1]["message"] == "token=[已隐藏] 发送失败"
    assert detail["logs"] == [{
        "at": "2026-07-20T01:01:59Z",
        "level": "ERROR",
        "message": "[本机路径已隐藏] token=[已隐藏] 发送异常",
    }]


def test_prefect_detail_service_falls_back_to_persisted_summary_when_prefect_is_unavailable() -> None:
    run = {
        "id": "monitor-run-1",
        "source": "prefect",
        "external_run_id": "prefect-run-1",
        "status": "failed",
        "current_step": "运行失败",
        "business_error_summary": "已同步的失败摘要",
        "steps": [],
        "logs": [],
    }

    def unavailable(_method: str, _path: str, _payload: dict[str, Any] | None = None) -> Any:
        raise OSError("connection refused")

    detail = PrefectMonitorDetailService(request=unavailable).get_detail(run)

    assert detail["detail_available"] is False
    assert detail["detail_message"] == "Prefect 详情暂不可用，正在显示已同步摘要。"
    assert detail["business_error_summary"] == "已同步的失败摘要"
    assert detail["steps"] == []



def test_prefect_detail_service_keeps_startup_failure_technical_but_business_readable() -> None:
    def request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        if path == "flow_runs/prefect-run-1":
            return {
                "id": "prefect-run-1",
                "state": {"type": "FAILED", "message": "Flow run could not start: unhandled errors in a TaskGroup (1 sub-exception)"},
            }
        if path in {"task_runs/filter", "logs/filter"}:
            return []
        raise AssertionError(f"unexpected request: {method} {path}")

    detail = PrefectMonitorDetailService(request=request).get_detail({
        "id": "monitor-run-1",
        "source": "prefect",
        "external_run_id": "prefect-run-1",
        "status": "failed",
        "current_step": "运行失败",
    })

    assert detail["current_step"] == "调度初始化"
    assert detail["business_error_summary"] == "调度服务未能启动本次通报，未开始执行。"
    assert detail["technical_error_summary"] == "Flow run could not start: unhandled errors in a TaskGroup (1 sub-exception)"