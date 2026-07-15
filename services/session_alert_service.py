from __future__ import annotations

import json
import logging
import re
import socket
from datetime import datetime
from pathlib import Path
from threading import Thread
from uuid import uuid4

from infrastructure.wecom_client import send_text
from services.runtime_paths import resolve_runtime_relative_path
from services.session_manager import file_lock


PROJECT_DIR = Path(__file__).resolve().parents[1]
WECOM_WEBHOOK_PATTERN = re.compile(
    r"(?i)https?://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?[^\s<>'\"]+"
)
SENSITIVE_KEYWORDS = (
    "cookie",
    "token",
    "storage",
    "password",
    "credential",
    "authorization",
    "secret",
    "username",
    "webhook",
    "jsessionid",
    "accesstoken",
    "uaptoken",
    "用户名",
    "stdout",
    "stderr",
)
REDACTED_SENSITIVE_DETAIL = "<redacted sensitive detail>"
LOGGER = logging.getLogger(__name__)


def _resolve(path_value, base_dir):
    path = Path(path_value)
    return path if path.is_absolute() else resolve_runtime_relative_path(path)


def _read_state(path):
    if not path.exists():
        return {"active_incident_key": None}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _redact(value):
    text = str(value)
    normalized = text.casefold()
    if WECOM_WEBHOOK_PATTERN.search(text) or any(keyword in normalized for keyword in SENSITIVE_KEYWORDS):
        return REDACTED_SENSITIVE_DETAIL
    return text


def _display(value, default="未提供"):
    if value is None or value == "":
        return default
    return _redact(value)


def _require_send_success(result):
    if isinstance(result, dict) and result.get("errcode") not in (None, 0, "0"):
        raise RuntimeError("WeCom session alert send failed")
    return result


def _incident_lock_path(config, state_path, base_dir):
    configured = config.get("incident_lock_path")
    if configured:
        return _resolve(configured, base_dir)
    return state_path.with_suffix(state_path.suffix + ".lock")


def _incident_transaction(config, state_path, base_dir):
    return file_lock(
        _incident_lock_path(config, state_path, base_dir),
        wait_seconds=float(config.get("incident_lock_wait_seconds", 120)),
        poll_seconds=float(config.get("incident_lock_poll_seconds", 0.05)),
        stale_seconds=float(config.get("incident_lock_stale_seconds", 300)),
        lock_label="会话告警锁",
    )


def _run_notification_safely(notification, *, notification_type):
    try:
        return notification()
    except Exception as exc:  # Alert delivery must not replace the primary Flow result.
        LOGGER.warning(
            "企业微信会话%s通知失败: notification_error_type=%s",
            notification_type,
            type(exc).__name__,
        )
        return None


def dispatch_session_notification(notification, *, notification_type):
    """Dispatch a session notification without blocking the calling Flow."""
    thread = Thread(
        target=_run_notification_safely,
        kwargs={"notification": notification, "notification_type": notification_type},
        daemon=True,
        name=f"session-{notification_type}-notification",
    )
    thread.start()
    return thread


def notify_session_failure(config, incident, *, base_dir=PROJECT_DIR, sender=send_text):
    state_path = _resolve(config["incident_state_path"], base_dir)
    incident_key = _redact(incident["incident_key"])
    with _incident_transaction(config, state_path, base_dir):
        state = _read_state(state_path)
        if state.get("active_incident_key") == incident_key:
            return {"sent": False, "suppressed": True}

        host_name = _redact(config.get("host_name") or socket.gethostname())
        errors = "；".join(_redact(item) for item in incident.get("errors", [])) or "未提供"
        failed_stages = ", ".join(_redact(item) for item in incident.get("failed_stages", [])) or "未提供"
        message = (
            f"[共享会话故障告警]\n主机: {host_name}\n"
            f"Flow: {_display(incident.get('flow_name'))}\n"
            f"Flow Run: {_display(incident.get('flow_run_name'))}\n"
            f"Flow Run ID: {_display(incident.get('flow_run_id'))}\n"
            f"来源: {_display(incident.get('trigger_source'))}\n"
            f"故障分类: {_display(incident.get('failure_category'))}\n"
            f"判定依据: {_display(incident.get('failure_reason'))}\n"
            f"受影响阶段: {failed_stages}\n"
            f"登录尝试次数: {_display(incident.get('attempt_count', 0))}\n"
            f"错误摘要: {errors}\n"
            f"后续检查: {_display(incident.get('next_scheduled_at'))}"
        )
        result = _require_send_success(sender(config["webhook_url"], message, timeout=30))
        _write_state(
            state_path,
            {
                "active_incident_key": incident_key,
                "failed_at": datetime.now().astimezone().isoformat(),
                "status": "active",
                "failure_category": _redact(incident["failure_category"]),
                "flow_name": _redact(incident.get("flow_name")),
                "flow_run_name": _redact(incident.get("flow_run_name")),
                "flow_run_id": _redact(incident.get("flow_run_id")),
                "trigger_source": _redact(incident["trigger_source"]),
                "failed_stages": failed_stages,
                "failure_category": _redact(incident.get("failure_category")),
                "failure_reason": _redact(incident.get("failure_reason")),
            },
        )
        return {"sent": True, "suppressed": False, "result": result}


def notify_session_recovery(config, recovery, *, base_dir=PROJECT_DIR, sender=send_text):
    state_path = _resolve(config["incident_state_path"], base_dir)
    with _incident_transaction(config, state_path, base_dir):
        state = _read_state(state_path)
        incident_key = _redact(recovery.get("incident_key") or state.get("active_incident_key"))
        if state.get("active_incident_key") != incident_key:
            return {"sent": False, "suppressed": True}

        message = (
            f"[共享会话恢复通知]\n"
            f"原故障分类: {_display(state.get('failure_category'))}\n"
            f"原判定依据: {_display(state.get('failure_reason'))}\n"
            f"原故障 Flow: {_display(state.get('flow_name'))}\n"
            f"原故障 Flow Run: {_display(state.get('flow_run_name'))}\n"
            f"原故障 Flow Run ID: {_display(state.get('flow_run_id'))}\n"
            f"受影响阶段: {_display(state.get('failed_stages'))}\n"
            f"恢复确认来源: {_display(recovery.get('trigger_source'))}\n"
            f"恢复确认 Flow: {_display(recovery.get('flow_name'))}\n"
            f"恢复确认 Run: {_display(recovery.get('flow_run_name'))}\n"
            f"恢复确认 Run ID: {_display(recovery.get('flow_run_id'))}"
        )
        result = _require_send_success(sender(config["webhook_url"], message, timeout=30))
        _write_state(
            state_path,
            {
                "active_incident_key": None,
                "recovered_at": datetime.now().astimezone().isoformat(),
                "status": "healthy",
            },
        )
        return {"sent": True, "suppressed": False, "result": result}
