"""Fetch a selected Prefect Flow Run's operational detail on demand."""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from typing import Any

from backend.services.monitor_event_service import sanitize_monitor_text
from backend.services.prefect_monitor_failure import classify_prefect_failure
from backend.services.prefect_runner import _prefect_api_request


DETAIL_UNAVAILABLE_MESSAGE = "Prefect 详情暂不可用，正在显示已同步摘要。"


def _state_type(payload: dict[str, Any]) -> str:
    state = payload.get("state") or {}
    return str(payload.get("state_type") or state.get("type") or state.get("name") or "").upper()


def _task_status(payload: dict[str, Any]) -> str:
    state = _state_type(payload)
    if state == "COMPLETED":
        return "completed"
    if state in {"FAILED", "CRASHED", "CANCELLED"}:
        return "failed"
    if state in {"RUNNING", "PENDING"}:
        return "running"
    return "pending"


def _log_level(value: Any) -> str:
    if isinstance(value, str) and not value.isdigit():
        normalized = value.upper()
        return normalized if normalized in {"INFO", "WARN", "ERROR"} else "INFO"
    try:
        level = int(value)
    except (TypeError, ValueError):
        return "INFO"
    if level >= 40:
        return "ERROR"
    if level >= 30:
        return "WARN"
    return "INFO"


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [item for item in payload.get("items", []) if isinstance(item, dict)]
    return []


class PrefectMonitorDetailService:
    """Read fresh task and log data without touching Prefect's PostgreSQL tables."""

    def __init__(
        self,
        *,
        request: Callable[[str, str, Any | None], Any] = _prefect_api_request,
    ) -> None:
        self._request = request

    def get_detail(self, run: dict[str, Any]) -> dict[str, Any]:
        fallback = {
            **run,
            "detail_available": False,
            "detail_message": DETAIL_UNAVAILABLE_MESSAGE,
        }
        flow_run_id = str(run.get("external_run_id") or "").strip()
        if run.get("source") != "prefect" or not flow_run_id:
            return fallback

        try:
            quoted_id = urllib.parse.quote(flow_run_id, safe="")
            flow_run = self._request("GET", f"flow_runs/{quoted_id}") or {}
            task_runs = _items(self._request("POST", "task_runs/filter", {
                "task_runs": {"flow_run_id": {"any_": [flow_run_id]}},
                "limit": 50,
            }))
            logs = _items(self._request("POST", "logs/filter", {
                "logs": {"flow_run_id": {"any_": [flow_run_id]}},
                "limit": 20,
                "sort": "TIMESTAMP_ASC",
            }))
        except (OSError, TimeoutError, ValueError):
            return fallback

        steps = [{
            "name": str(task.get("name") or "未命名步骤"),
            "status": _task_status(task),
            "message": sanitize_monitor_text(str((task.get("state") or {}).get("message") or "")),
            "started_at": task.get("start_time"),
            "finished_at": task.get("end_time"),
        } for task in task_runs]
        safe_logs = [{
            "at": log.get("timestamp") or log.get("created"),
            "level": _log_level(log.get("level")),
            "message": sanitize_monitor_text(str(log.get("message") or "")),
        } for log in logs]
        failed_steps = [step for step in steps if step["status"] == "failed"]
        error_logs = [log for log in safe_logs if log["level"] == "ERROR"]
        flow_state = flow_run.get("state") or {}
        flow_message = sanitize_monitor_text(str(flow_state.get("message") or ""))
        failure_message = (
            flow_message
            or (failed_steps[0]["message"] if failed_steps and failed_steps[0]["message"] else "")
            or (error_logs[-1]["message"] if error_logs else "")
            or run.get("technical_error_summary")
            or run.get("business_error_summary")
        )
        failure = classify_prefect_failure(str(failure_message or ""))

        detail = {
            **run,
            "started_at": flow_run.get("start_time") or run.get("started_at"),
            "finished_at": flow_run.get("end_time") or run.get("finished_at"),
            "steps": steps,
            "logs": safe_logs,
            "detail_available": True,
        }
        if str(run.get("status")) == "failed":
            detail["current_step"] = (
                failure.current_step if failure.is_startup_failure
                else failed_steps[0]["name"] if failed_steps
                else run.get("current_step") or failure.current_step
            )
            detail["business_error_summary"] = failure.business_summary or run.get("business_error_summary")
            detail["technical_error_summary"] = failure.technical_summary or run.get("technical_error_summary")
        return detail
