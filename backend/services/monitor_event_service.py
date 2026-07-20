"""Normalized, sanitized monitoring events shared by Prefect and web operations.

The service deliberately owns the application's monitor data model.  It never
reads or writes Prefect's internal PostgreSQL tables; Prefect data is supplied
through the adapter layer and normalized here before persistence.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable, Protocol
from uuid import uuid4


RUN_STATUSES = {"scheduled", "running", "succeeded", "failed", "retrying", "cancelled"}
LOG_LEVELS = {"INFO", "WARN", "ERROR"}


class MonitorStore(Protocol):
    def create_or_update_run(self, run: dict[str, Any]) -> dict[str, Any]: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...
    def find_by_external_id(self, source: str, external_run_id: str) -> dict[str, Any] | None: ...
    def append_step(self, run_id: str, step: dict[str, Any]) -> dict[str, Any]: ...
    def append_event(self, run_id: str, event: dict[str, Any]) -> dict[str, Any]: ...
    def apply_prefect_event(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        normalized_run: dict[str, Any],
        message: str,
    ) -> tuple[dict[str, Any], bool]: ...
    def list_runs(self) -> list[dict[str, Any]]: ...


_SECRET_VALUE = re.compile(r"(?i)\b(token|cookie|password|authorization|webhook)\s*[:=]\s*[^\s,;]+")
_URL = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)


def sanitize_monitor_text(value: str | None) -> str:
    """Keep operational context while removing secrets and internal URLs."""
    text = str(value or "")
    text = _SECRET_VALUE.sub(lambda match: f"{match.group(1)}=[已隐藏]", text)
    text = re.sub(r"(?i)(?:[a-z]:\\|\\\\)[^\s,;]+", "[本机路径已隐藏]", text)
    return _URL.sub("[内部地址已隐藏]", text)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _parse_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class MemoryMonitorStore:
    """Small deterministic store used in tests and local development only."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.steps: dict[str, list[dict[str, Any]]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.sequence = 0
        self.source_event_ids: set[str] = set()

    def create_or_update_run(self, run: dict[str, Any]) -> dict[str, Any]:
        self.runs[run["id"]] = deepcopy(run)
        return deepcopy(run)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.runs.get(run_id)
        if run is None:
            return None
        payload = deepcopy(run)
        payload["steps"] = deepcopy(self.steps.get(run_id, []))
        payload["logs"] = deepcopy(self.events.get(run_id, []))
        return payload

    def find_by_external_id(self, source: str, external_run_id: str) -> dict[str, Any] | None:
        for run in self.runs.values():
            if run["source"] == source and run.get("external_run_id") == external_run_id:
                return deepcopy(run)
        return None

    def append_step(self, run_id: str, step: dict[str, Any]) -> dict[str, Any]:
        steps = self.steps.setdefault(run_id, [])
        for index, current in enumerate(steps):
            if current["name"] == step["name"]:
                steps[index] = deepcopy(step)
                return deepcopy(step)
        steps.append(deepcopy(step))
        return deepcopy(step)

    def append_event(self, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        self.sequence += 1
        event = {**event, "stream_sequence": self.sequence}
        self.events.setdefault(run_id, []).append(deepcopy(event))
        return deepcopy(event)

    def apply_prefect_event(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        normalized_run: dict[str, Any],
        message: str,
    ) -> tuple[dict[str, Any], bool]:
        existing = self.find_by_external_id(
            "prefect",
            normalized_run["external_run_id"],
        )
        if event_id in self.source_event_ids:
            return existing or deepcopy(normalized_run), False
        self.source_event_ids.add(event_id)
        if existing is not None:
            existing_at = _parse_iso(existing.get("state_occurred_at"))
            if existing_at is not None and occurred_at <= existing_at:
                return existing, False

        run = {
            **(existing or {}),
            **normalized_run,
            "state_occurred_at": _iso(occurred_at),
        }
        saved = self.create_or_update_run(run)
        self.append_event(saved["id"], {
            "at": _iso(occurred_at),
            "level": "INFO",
            "message": sanitize_monitor_text(message),
            "details": "",
            "source_event_id": event_id,
        })
        return saved, True

    def list_runs(self) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in self.runs.values()]


class MonitorEventService:
    def __init__(self, *, store: MonitorStore, now: Callable[[], datetime] = datetime.now) -> None:
        self.store = store
        self.now = now

    def start_run(self, *, source: str, external_run_id: str, task_name: str, trigger: str, scheduled_at: datetime | None = None) -> dict[str, Any]:
        return self._save_run({
            "id": f"mon_{uuid4().hex}",
            "source": source,
            "external_run_id": external_run_id,
            "task_name": task_name,
            "target_kind": "web",
            "target_id": f"web-{external_run_id}",
            "trigger": trigger,
            "status": "running",
            "scheduled_at": _iso(scheduled_at),
            "started_at": _iso(self.now()),
            "finished_at": None,
            "current_step": "准备参数",
            "business_error_summary": None,
            "technical_error_summary": None,
        })

    def upsert_prefect_run(
        self,
        *,
        external_run_id: str,
        task_name: str,
        status: str,
        target_kind: str = "system",
        target_id: str | None = None,
        scheduled_at: datetime | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        current_step: str = "尚未开始",
        business_error_summary: str | None = None,
        technical_error_summary: str | None = None,
    ) -> dict[str, Any]:
        if status not in RUN_STATUSES:
            raise ValueError(f"不支持的监控运行状态: {status}")
        existing = self.store.find_by_external_id("prefect", external_run_id)
        run = existing or {
            "id": f"mon_{uuid4().hex}",
            "source": "prefect",
            "external_run_id": external_run_id,
            "task_name": task_name,
            "target_kind": target_kind,
            "target_id": target_id or f"prefect-{external_run_id}",
            "trigger": "定时调度",
            "business_error_summary": None,
            "technical_error_summary": None,
        }
        run.update({
            "task_name": task_name,
            "target_kind": target_kind,
            "target_id": target_id or run.get("target_id") or f"prefect-{external_run_id}",
            "status": status,
            "scheduled_at": _iso(scheduled_at) or run.get("scheduled_at"),
            "started_at": _iso(started_at) or run.get("started_at"),
            "finished_at": _iso(finished_at) or run.get("finished_at"),
            "current_step": current_step,
        })
        if status == "failed":
            if business_error_summary:
                run["business_error_summary"] = sanitize_monitor_text(business_error_summary)
            if technical_error_summary:
                run["technical_error_summary"] = sanitize_monitor_text(technical_error_summary)
        return self._save_run(run)

    def update_step(self, run_id: str, *, name: str, status: str, message: str = "") -> dict[str, Any]:
        run = self._require_run(run_id)
        if status not in {"pending", "running", "completed", "failed"}:
            raise ValueError(f"不支持的步骤状态: {status}")
        now = self.now()
        detail = self.store.get_run(run_id) or {}
        existing = next((item for item in detail.get("steps", []) if item["name"] == name), None)
        step = {
            "name": name,
            "status": status,
            "message": sanitize_monitor_text(message),
            "started_at": existing.get("started_at") if existing else (_iso(now) if status in {"running", "completed", "failed"} else None),
            "finished_at": _iso(now) if status in {"completed", "failed"} else None,
        }
        stored = self.store.append_step(run_id, step)
        run["current_step"] = name
        if status == "failed":
            run["status"] = "failed"
            run["business_error_summary"] = sanitize_monitor_text(message)
        self._save_run(run)
        return stored

    def append_log(self, run_id: str, *, level: str, message: str, details: str | None = None) -> dict[str, Any]:
        self._require_run(run_id)
        normalized_level = level.upper()
        if normalized_level not in LOG_LEVELS:
            raise ValueError(f"不支持的日志级别: {level}")
        return self.store.append_event(run_id, {
            "at": _iso(self.now()),
            "level": normalized_level,
            "message": sanitize_monitor_text(message),
            "details": sanitize_monitor_text(details),
        })

    def finish_run(self, run_id: str, *, status: str, current_step: str, business_error_summary: str | None = None, technical_error_summary: str | None = None) -> dict[str, Any]:
        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError(f"结束状态不合法: {status}")
        run = self._require_run(run_id)
        now = self.now()
        detail = self.store.get_run(run_id) or {}
        for step in detail.get("steps", []):
            if step.get("status") == "running":
                self.store.append_step(run_id, {**step, "status": "completed" if status == "succeeded" else "failed", "finished_at": _iso(now)})
        run.update({
            "status": status,
            "current_step": current_step,
            "finished_at": _iso(now),
            "business_error_summary": sanitize_monitor_text(business_error_summary),
            "technical_error_summary": sanitize_monitor_text(technical_error_summary),
        })
        return self._save_run(run)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._require_run(run_id, include_detail=True)

    def list_scheduled(self) -> dict[str, Any]:
        scheduled = [run for run in self.store.list_runs() if run.get("status") == "scheduled"]
        scheduled.sort(key=lambda item: item.get("scheduled_at") or "9999-12-31")
        return {"total": len(scheduled), "items": scheduled}

    def _require_run(self, run_id: str, *, include_detail: bool = False) -> dict[str, Any]:
        run = self.store.get_run(run_id) if include_detail else next((item for item in self.store.list_runs() if item["id"] == run_id), None)
        if run is None:
            raise KeyError(f"监控运行不存在: {run_id}")
        return run

    def _save_run(self, run: dict[str, Any]) -> dict[str, Any]:
        return self.store.create_or_update_run(run)
