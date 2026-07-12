"""Shared incident reporting for business-triggered session failures."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
from typing import TypeVar

from prefect.runtime import flow_run

from services.session_alert_service import notify_session_failure, notify_session_recovery
from services.session_manager import (
    SessionInfrastructureError,
    SessionLoginError,
    summarize_login_failure,
)
from services.session_retry_service import is_session_expired_error
from utils.config_loader import load_json_with_local_override


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_KEEPER_CONFIG_PATH = "config/modules/session_keeper.json"
T = TypeVar("T")
LOGGER = logging.getLogger(__name__)


def _is_reportable_session_failure(exc):
    return isinstance(exc, (SessionLoginError, SessionInfrastructureError)) or (
        isinstance(exc, RuntimeError) and is_session_expired_error(exc)
    )


def build_business_session_incident(exc, *, trigger_source, flow_run_id):
    if isinstance(exc, SessionLoginError):
        category = "authentication"
        errors = exc.errors
        attempt_count = exc.attempt_count
    elif isinstance(exc, SessionInfrastructureError):
        category = "infrastructure"
        errors = [str(exc)]
        attempt_count = 0
    elif isinstance(exc, RuntimeError) and is_session_expired_error(exc):
        category = "authentication"
        errors = [summarize_login_failure(exc)]
        attempt_count = 0
    else:
        raise TypeError("unsupported session failure")
    return {
        "incident_key": f"{category}:shared-session",
        "trigger_source": trigger_source,
        "failure_category": category,
        "failed_stages": ["shared-session"],
        "attempt_count": attempt_count,
        "errors": errors,
        "flow_run_id": flow_run_id,
        "next_scheduled_at": "next session-keeper schedule",
    }


def report_business_session_failure(
    exc,
    *,
    trigger_source,
    base_dir=PROJECT_DIR,
    keeper_config_path=DEFAULT_KEEPER_CONFIG_PATH,
):
    keeper, _ = load_json_with_local_override(base_dir / keeper_config_path)
    alert_source, _ = load_json_with_local_override(base_dir / keeper["alert_config_path"])
    alert_config = {
        "webhook_url": alert_source["wecom"]["webhook_url"],
        "incident_state_path": keeper["incident_state_path"],
    }
    incident = build_business_session_incident(
        exc,
        trigger_source=trigger_source,
        flow_run_id=str(flow_run.id or "manual"),
    )
    return notify_session_failure(alert_config, incident, base_dir=base_dir)


def recover_business_session_incidents(
    alert_config,
    *,
    flow_run_id,
    base_dir=PROJECT_DIR,
    notifier=notify_session_recovery,
):
    results = {}
    for category in ("authentication", "infrastructure"):
        incident_key = f"{category}:shared-session"
        results[category] = notifier(
            alert_config,
            {"incident_key": incident_key, "flow_run_id": flow_run_id},
            base_dir=base_dir,
        )
    return results


def report_business_session_recovery(
    *,
    trigger_source,
    base_dir=PROJECT_DIR,
    keeper_config_path=DEFAULT_KEEPER_CONFIG_PATH,
):
    del trigger_source
    keeper, _ = load_json_with_local_override(base_dir / keeper_config_path)
    alert_source, _ = load_json_with_local_override(base_dir / keeper["alert_config_path"])
    return recover_business_session_incidents(
        {
            "webhook_url": alert_source["wecom"]["webhook_url"],
            "incident_state_path": keeper["incident_state_path"],
        },
        flow_run_id=str(flow_run.id or "manual"),
        base_dir=base_dir,
    )


def _run_notification_safely(notification, *, failure_type):
    try:
        return notification()
    except Exception as exc:
        LOGGER.warning(
            "会话%s通知失败，保留原始业务结果: notification_error_type=%s",
            failure_type,
            type(exc).__name__,
        )
        return None


def run_with_business_session_reporting(
    operation: Callable[[], T],
    *,
    trigger_source: str,
    reporter=None,
    recover_on_success=False,
    recoverer=None,
) -> T:
    try:
        result = operation()
    except RuntimeError as exc:
        if not _is_reportable_session_failure(exc):
            raise
        primary_failure = exc
        active_reporter = reporter or report_business_session_failure
        _run_notification_safely(
            lambda: active_reporter(
                primary_failure,
                trigger_source=trigger_source,
            ),
            failure_type="失败",
        )
        raise
    if recover_on_success:
        active_recoverer = recoverer or report_business_session_recovery
        _run_notification_safely(
            lambda: active_recoverer(trigger_source=trigger_source),
            failure_type="恢复",
        )
    return result
