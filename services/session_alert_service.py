from __future__ import annotations

import json
import re
import socket
from datetime import datetime
from pathlib import Path

from infrastructure.wecom_client import send_text


PROJECT_DIR = Path(__file__).resolve().parents[1]
SECRET_PATTERN = re.compile(r"(?i)(cookie|token|password|webhook)(\s*[=:]\s*)\S+")


def _resolve(path_value, base_dir):
    path = Path(path_value)
    return path if path.is_absolute() else base_dir / path


def _read_state(path):
    if not path.exists():
        return {"active_incident_key": None}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _redact(value):
    return SECRET_PATTERN.sub(r"\1\2<redacted>", str(value))


def notify_session_failure(config, incident, *, base_dir=PROJECT_DIR, sender=send_text):
    state_path = _resolve(config["incident_state_path"], base_dir)
    state = _read_state(state_path)
    incident_key = _redact(incident["incident_key"])
    if state.get("active_incident_key") == incident_key:
        return {"sent": False, "suppressed": True}

    host_name = _redact(config.get("host_name") or socket.gethostname())
    errors = "；".join(_redact(item) for item in incident.get("errors", []))
    failed_stages = ", ".join(_redact(item) for item in incident.get("failed_stages", []))
    message = (
        f"[自动登录告警]\n主机: {host_name}\n"
        f"来源: {_redact(incident['trigger_source'])}\n"
        f"分类: {_redact(incident['failure_category'])}\n"
        f"阶段: {failed_stages}\n"
        f"登录尝试: {_redact(incident.get('attempt_count', 0))}\n"
        f"错误: {errors}\n"
        f"Flow Run ID: {_redact(incident.get('flow_run_id'))}\n"
        f"下次调度: {_redact(incident.get('next_scheduled_at'))}"
    )
    result = sender(config["webhook_url"], message, timeout=30)
    _write_state(
        state_path,
        {
            "active_incident_key": incident_key,
            "failed_at": datetime.now().astimezone().isoformat(),
            "status": "active",
            "failure_category": _redact(incident["failure_category"]),
        },
    )
    return {"sent": True, "suppressed": False, "result": result}


def notify_session_recovery(config, recovery, *, base_dir=PROJECT_DIR, sender=send_text):
    state_path = _resolve(config["incident_state_path"], base_dir)
    state = _read_state(state_path)
    incident_key = _redact(recovery["incident_key"])
    if state.get("active_incident_key") != incident_key:
        return {"sent": False, "suppressed": True}

    message = (
        f"[自动登录恢复]\n故障: {incident_key}\n"
        f"Flow Run ID: {_redact(recovery.get('flow_run_id'))}"
    )
    result = sender(config["webhook_url"], message, timeout=30)
    _write_state(
        state_path,
        {
            "active_incident_key": None,
            "recovered_at": datetime.now().astimezone().isoformat(),
            "status": "healthy",
        },
    )
    return {"sent": True, "suppressed": False, "result": result}
