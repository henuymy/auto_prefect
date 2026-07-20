"""Safe, frontend-oriented representations of persisted monitor data."""

from __future__ import annotations

import re
from typing import Any


def _target_id(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", name).strip("-")
    return normalized[:64] or "unknown"


def _duration_seconds(run: dict[str, Any]) -> int | None:
    started, finished = run.get("started_at"), run.get("finished_at")
    if not started or not finished:
        return None
    from datetime import datetime
    return max(0, int((datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()))


def to_frontend_run(run: dict[str, Any]) -> dict[str, Any]:
    status = "skipped" if run.get("status") == "cancelled" else run.get("status", "scheduled")
    steps = [
        {
            "name": step["name"],
            "status": {"running": "active", "completed": "completed", "failed": "failed"}.get(step.get("status"), "pending"),
            "startedAt": step.get("started_at"),
            "finishedAt": step.get("finished_at"),
            "message": step.get("message") or "",
        }
        for step in run.get("steps", [])
    ]
    error = None
    if status == "failed":
        error = {
            "category": "系统",
            "failedStep": run.get("current_step") or "未知步骤",
            "businessSummary": run.get("business_error_summary") or "运行失败，暂无业务摘要。",
            "technicalSummary": run.get("technical_error_summary") or "已隐藏敏感技术细节。",
            "retryCount": 0,
        }
    return {
        "id": run["id"],
        "targetId": run.get("target_id") or _target_id(run.get("task_name") or "未命名运行"),
        "target": run.get("task_name") or "未命名运行",
        "trigger": "web" if run.get("source") == "web" else "scheduled",
        "source": "web" if run.get("source") == "web" else "prefect",
        "status": status,
        "scheduledAt": run.get("scheduled_at"),
        "startedAt": run.get("started_at"),
        "finishedAt": run.get("finished_at"),
        "currentStep": run.get("current_step") or "尚未开始",
        "nextStep": run.get("current_step") or "尚未开始",
        "durationSeconds": _duration_seconds(run),
        "steps": steps,
        "logs": [{"at": event.get("at"), "level": event.get("level"), "message": event.get("message")} for event in run.get("logs", [])],
        **({"detailAvailable": False, "detailMessage": run.get("detail_message") or "Prefect 详情暂不可用，正在显示已同步摘要。"} if run.get("detail_available") is False else {}),
        **({"error": error} if error else {}),
    }


def to_frontend_pending_queue(runs: list[dict[str, Any]]) -> dict[str, Any]:
    scheduled = sorted(
        (item for item in runs if item.get("status") == "scheduled"),
        key=lambda item: item.get("scheduled_at") or "9999-12-31",
    )
    return {
        "scopeLabel": "数据库当前 Scheduled",
        "total": len(scheduled),
        "items": [to_frontend_run(item) for item in scheduled],
    }
