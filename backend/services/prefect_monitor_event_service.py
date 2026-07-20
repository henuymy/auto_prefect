"""Normalize Prefect Automation events into durable report-monitor updates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping
from uuid import uuid4

from backend.services.monitor_event_service import MonitorStore, sanitize_monitor_text
from backend.services.prefect_monitor_failure import classify_prefect_failure
from backend.services.prefect_monitor_adapter import (
    STATE_MAP,
    _as_datetime,
    _target_identity,
)


class InvalidPrefectMonitorEvent(ValueError):
    """Raised when a webhook payload is not a valid Flow Run event."""


@dataclass(frozen=True)
class ProcessedPrefectEvent:
    accepted: bool
    duplicate: bool
    run: dict[str, Any] | None


def _flow_run_id(event: Mapping[str, Any]) -> str | None:
    resource_id = str((event.get("resource") or {}).get("prefect.resource.id") or "")
    prefix = "prefect.flow-run."
    return resource_id.removeprefix(prefix) if resource_id.startswith(prefix) else None


def _event_status(event_name: str) -> str | None:
    if not event_name.startswith("prefect.flow-run."):
        return None
    return STATE_MAP.get(event_name.rsplit(".", maxsplit=1)[-1].upper())


def _current_step(status: str) -> str:
    if status == "scheduled":
        return "尚未开始"
    if status in {"running", "retrying"}:
        return "运行中"
    if status == "failed":
        return "运行失败"
    if status == "cancelled":
        return "已取消"
    return "已完成"


def _iso_datetime(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


class PrefectMonitorEventProcessor:
    def __init__(
        self,
        *,
        store: MonitorStore,
        fetch_flow_run: Callable[[str], Mapping[str, Any]],
        fetch_deployment: Callable[[str], Mapping[str, Any]],
        fetch_flow: Callable[[str], Mapping[str, Any]],
    ) -> None:
        self.store = store
        self.fetch_flow_run = fetch_flow_run
        self.fetch_deployment = fetch_deployment
        self.fetch_flow = fetch_flow

    def process(self, event: Mapping[str, Any]) -> ProcessedPrefectEvent:
        event_id = str(event.get("id") or "").strip()
        occurred_at = _as_datetime(str(event.get("occurred") or ""))
        flow_run_id = _flow_run_id(event)
        event_name = str(event.get("event") or "")
        status = _event_status(event_name)
        if not event_id or occurred_at is None or not flow_run_id:
            raise InvalidPrefectMonitorEvent("事件缺少 id、occurred 或 Flow Run 资源 ID")
        if status is None:
            return ProcessedPrefectEvent(accepted=False, duplicate=False, run=None)

        flow_run = dict(self.fetch_flow_run(flow_run_id))
        flow_run["id"] = flow_run_id
        deployment_id = str(flow_run.get("deployment_id") or "")
        if not deployment_id:
            raise InvalidPrefectMonitorEvent("Flow Run 缺少 deployment_id")
        deployment = dict(self.fetch_deployment(deployment_id))
        flow_id = str(deployment.get("flow_id") or "")
        if not flow_id:
            raise InvalidPrefectMonitorEvent("Deployment 缺少 flow_id")
        flow = dict(self.fetch_flow(flow_id))
        target_kind, target_id, task_name = _target_identity(
            flow_run,
            {deployment_id: deployment},
            {flow_id: flow},
        )
        if target_kind != "report":
            return ProcessedPrefectEvent(accepted=False, duplicate=False, run=None)

        failure = classify_prefect_failure(str(
            (flow_run.get("state") or {}).get("message")
            or flow_run.get("state_message") or ""
        ))
        normalized_run = {
            "id": f"mon_{uuid4().hex}",
            "source": "prefect",
            "external_run_id": flow_run_id,
            "task_name": task_name,
            "target_kind": target_kind,
            "target_id": target_id,
            "trigger": "定时调度",
            "status": status,
            "scheduled_at": _iso_datetime(_as_datetime(flow_run.get("expected_start_time"))),
            "started_at": _iso_datetime(_as_datetime(flow_run.get("start_time"))),
            "finished_at": _iso_datetime(_as_datetime(flow_run.get("end_time"))),
            "current_step": failure.current_step if status == "failed" else _current_step(status),
            "business_error_summary": failure.business_summary if status == "failed" else None,
            "technical_error_summary": failure.technical_summary if status == "failed" else None,
        }
        run, changed = self.store.apply_prefect_event(
            event_id=event_id,
            occurred_at=occurred_at,
            normalized_run=normalized_run,
            message=sanitize_monitor_text(event_name),
        )
        return ProcessedPrefectEvent(
            accepted=changed,
            duplicate=not changed,
            run=run,
        )
