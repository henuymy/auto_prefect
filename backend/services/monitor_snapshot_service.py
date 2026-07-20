"""Build report-only monitor snapshots and incremental stream messages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from backend.services.monitor_api_schema import (
    to_frontend_pending_queue,
    to_frontend_run,
)


REPORT_HISTORY_DAYS = 30
DISPLAYED_HISTORY_STATUSES = {"succeeded", "running", "failed"}


def _as_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def report_history_runs(
    runs: list[dict[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    cutoff = now.astimezone(timezone.utc) - timedelta(days=REPORT_HISTORY_DAYS)
    history = []
    for run in runs:
        reference_time = _as_utc(run.get("started_at") or run.get("scheduled_at"))
        if (
            run.get("target_kind") == "report"
            and run.get("status") in DISPLAYED_HISTORY_STATUSES
            and reference_time is not None
            and reference_time >= cutoff
        ):
            history.append(run)
    return history


def build_monitor_snapshot(
    runs: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    upstream: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    updated_at = now or datetime.now(timezone.utc)
    history = report_history_runs(runs, now=updated_at)
    return {
        "runs": [to_frontend_run(run) for run in history],
        "pendingQueue": to_frontend_pending_queue([
            run for run in runs if run.get("target_kind") == "report"
        ]),
        "summary": {
            "succeeded": sum(run["status"] == "succeeded" for run in history),
            "running": sum(run["status"] == "running" for run in history),
            "failed": sum(run["status"] == "failed" for run in history),
            "scheduled": 0,
        },
        "updatedAt": updated_at.isoformat(timespec="seconds"),
        "connected": True,
        "upstream": upstream or {
            "lastAcceptedAt": None,
            "lastReconciledAt": None,
            "lastErrorCategory": None,
        },
    }


def build_run_update(
    runs: list[dict[str, Any]],
    run_id: str,
    *,
    now: datetime | None = None,
    upstream: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    snapshot = build_monitor_snapshot(runs, now=now, upstream=upstream)
    changed = next((run for run in snapshot["runs"] if run["id"] == run_id), None)
    return {
        "type": "run.updated",
        "runId": run_id,
        "run": changed,
        "pendingQueue": snapshot["pendingQueue"],
        "summary": snapshot["summary"],
        "updatedAt": snapshot["updatedAt"],
        "connected": True,
        "upstream": snapshot["upstream"],
    }
