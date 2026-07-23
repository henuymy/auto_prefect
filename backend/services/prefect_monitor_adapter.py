"""Read-only Prefect API adapter for the monitor-center data model.

The adapter accepts Prefect API flow-run payloads and deliberately does not
query Prefect's PostgreSQL tables.  It is small enough to be called from a
scheduled sync job now, or replaced by an event consumer later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from backend.services.monitor_event_service import MonitorEventService
from backend.services.prefect_monitor_failure import classify_prefect_failure
from backend.services.prefect_runner import _prefect_api_request


STATE_MAP = {
    "SCHEDULED": "scheduled",
    "PENDING": "scheduled",
    "RUNNING": "running",
    "COMPLETED": "succeeded",
    "FAILED": "failed",
    "CRASHED": "failed",
    "CANCELLED": "cancelled",
    "CANCELLING": "cancelled",
}


def _as_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _state_name(flow_run: dict[str, Any]) -> str:
    state = flow_run.get("state") or {}
    return str(
        flow_run.get("state_type")
        or flow_run.get("state_name")
        or state.get("type")
        or state.get("name")
        or "SCHEDULED"
    ).upper()


def _target_identity(
    flow_run: Mapping[str, Any],
    deployments_by_id: Mapping[str, Mapping[str, Any]],
    flows_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str, str]:
    external_run_id = str(flow_run.get("id") or "")
    deployment_id = str(flow_run.get("deployment_id") or "")
    deployment = deployments_by_id.get(deployment_id, {})
    flow = flows_by_id.get(str(deployment.get("flow_id") or ""), {})
    deployment_name = str(deployment.get("name") or "").strip()

    if flow.get("name") == "auto-notify-flow" and deployment_name.startswith("notify-"):
        report_name = deployment_name.removeprefix("notify-").strip() or "未命名通报"
        return "report", f"report-{deployment_id}", f"通报 · {report_name}"

    target_id = f"prefect-{deployment_id or external_run_id}"
    return "system", target_id, deployment_name or str(flow_run.get("name") or "未命名运行")


class PrefectMonitorAdapter:
    def __init__(self, service: MonitorEventService) -> None:
        self.service = service

    def sync_flow_runs(
        self,
        flow_runs: Iterable[dict[str, Any]],
        *,
        deployments_by_id: Mapping[str, Mapping[str, Any]] | None = None,
        flows_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Normalize an already fetched Prefect flow-run list into monitor rows."""
        deployments_by_id = deployments_by_id or {}
        flows_by_id = flows_by_id or {}
        saved: list[dict[str, Any]] = []
        for flow_run in flow_runs:
            external_run_id = str(flow_run.get("id") or "")
            if not external_run_id:
                continue
            target_kind, target_id, task_name = _target_identity(
                flow_run,
                deployments_by_id,
                flows_by_id,
            )
            if target_kind != "report":
                continue
            status = STATE_MAP.get(_state_name(flow_run), "scheduled")
            state = flow_run.get("state") or {}
            failure = classify_prefect_failure(str(state.get("message") or flow_run.get("state_message") or ""))
            if status == "scheduled":
                current_step = "尚未开始"
            elif status in {"running", "retrying"}:
                current_step = "运行中"
            elif status == "failed":
                current_step = failure.current_step
            elif status == "cancelled":
                current_step = "已取消"
            else:
                current_step = "已完成"
            saved.append(self.service.upsert_prefect_run(
                external_run_id=external_run_id,
                task_name=task_name,
                status=status,
                target_kind=target_kind,
                target_id=target_id,
                scheduled_at=_as_datetime(flow_run.get("expected_start_time")),
                started_at=_as_datetime(flow_run.get("start_time")),
                finished_at=_as_datetime(flow_run.get("end_time")),
                current_step=current_step,
                business_error_summary=failure.business_summary if status == "failed" else None,
                technical_error_summary=failure.technical_summary if status == "failed" else None,
            ))
        return saved

    def fetch_and_sync(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Fetch a bounded flow-run window through Prefect's supported HTTP API."""
        flow_runs = _prefect_api_request("POST", "flow_runs/filter", {
            "limit": limit,
            "sort": "EXPECTED_START_TIME_DESC",
        })
        deployment_ids = sorted({
            str(run.get("deployment_id"))
            for run in flow_runs or []
            if run.get("deployment_id")
        })
        deployments = _prefect_api_request("POST", "deployments/filter", {
            "deployments": {"operator": "and_", "id": {"any_": deployment_ids}},
        }) if deployment_ids else []
        deployments_by_id = {
            str(deployment["id"]): deployment
            for deployment in deployments or []
            if deployment.get("id")
        }
        flow_ids = sorted({
            str(deployment.get("flow_id"))
            for deployment in deployments_by_id.values()
            if deployment.get("flow_id")
        })
        flows = _prefect_api_request("POST", "flows/filter", {
            "flows": {"operator": "and_", "id": {"any_": flow_ids}},
        }) if flow_ids else []
        flows_by_id = {
            str(flow["id"]): flow
            for flow in flows or []
            if flow.get("id")
        }
        return self.sync_flow_runs(
            flow_runs or [],
            deployments_by_id=deployments_by_id,
            flows_by_id=flows_by_id,
        )
