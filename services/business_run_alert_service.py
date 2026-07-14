"""Final business-run alerts, independent from session health incidents."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from infrastructure.wecom_client import send_text
from prefect.runtime import flow_run
from services.session_alert_service import _redact, _require_send_success
from services.session_manager import file_lock
from utils.config_loader import load_json_with_local_override


PROJECT_DIR = Path(__file__).resolve().parents[1]


def build_business_run_incident(
    exc: Exception,
    *,
    workload_type: str,
    workload_id: str,
    flow_name: str,
    flow_run_name: str,
    flow_run_id: str,
    failed_stage: str,
) -> dict:
    return {
        "incident_key": f"development:{workload_type}:{workload_id}:{flow_run_id}",
        "workload_type": workload_type,
        "workload_id": workload_id,
        "flow_name": flow_name,
        "flow_run_name": flow_run_name,
        "flow_run_id": flow_run_id,
        "failed_stage": failed_stage,
        "error_summary": _redact(f"{type(exc).__name__}: {exc}"),
    }


def notify_business_run_failure(config: dict, incident: dict, *, sender=send_text) -> dict:
    if str(config.get("environment") or os.getenv("AUTO_NOTIFY_ENVIRONMENT") or "production").lower() != "development":
        return {"sent": False, "suppressed": True, "reason": "environment"}

    state_path = Path(config["incident_state_path"])
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    with file_lock(lock_path, wait_seconds=120, poll_seconds=0.05, stale_seconds=300, lock_label="业务告警锁"):
        state = _read_state(state_path)
        key = incident["incident_key"]
        if key in state["sent_incidents"]:
            return {"sent": False, "suppressed": True, "reason": "duplicate"}

        message = (
            "[Development 业务 Run 失败]\n"
            f"业务类型: {_redact(incident['workload_type'])}\n"
            f"业务名称: {_redact(incident['workload_id'])}\n"
            f"失败环节: {_redact(incident['failed_stage'])}\n"
            f"最终原因: {_redact(incident['error_summary'])}\n"
            f"Flow: {_redact(incident['flow_name'])}\n"
            f"Flow Run: {_redact(incident['flow_run_name'])}\n"
            f"Flow Run ID: {_redact(incident['flow_run_id'])}"
        )
        result = _require_send_success(sender(config["webhook_url"], message, timeout=30))
        state["sent_incidents"][key] = {
            "sent_at": datetime.now().astimezone().isoformat(),
            "workload_type": _redact(incident["workload_type"]),
            "workload_id": _redact(incident["workload_id"]),
            "flow_run_id": _redact(incident["flow_run_id"]),
        }
        _write_state(state_path, state)
        return {"sent": True, "suppressed": False, "result": result}


def report_current_business_run_failure(
    exc: Exception,
    *,
    workload_type: str,
    workload_id: str,
    flow_name: str,
    failed_stage: str,
    base_dir: Path = PROJECT_DIR,
) -> dict:
    keeper, _ = load_json_with_local_override(base_dir / "config/modules/session_keeper.json")
    sender_config, _ = load_json_with_local_override(base_dir / keeper["alert_config_path"])
    incident = build_business_run_incident(
        exc,
        workload_type=workload_type,
        workload_id=workload_id,
        flow_name=flow_name,
        flow_run_name=str(getattr(flow_run, "name", None) or "manual"),
        flow_run_id=str(flow_run.id or "manual"),
        failed_stage=failed_stage,
    )
    return notify_business_run_failure(
        {
            "webhook_url": sender_config["wecom"]["webhook_url"],
            "environment": os.getenv("AUTO_NOTIFY_ENVIRONMENT", "production"),
            "incident_state_path": base_dir / "runtime/session/business-run-alerts.json",
        },
        incident,
    )


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {"sent_incidents": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) and isinstance(payload.get("sent_incidents"), dict) else {"sent_incidents": {}}


def _write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
