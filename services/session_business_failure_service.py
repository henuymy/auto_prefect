"""Shared incident reporting for business-triggered session failures."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from prefect.runtime import flow_run

from services.session_alert_service import notify_session_failure
from services.session_manager import SessionInfrastructureError, SessionLoginError
from utils.config_loader import load_json_with_local_override


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_KEEPER_CONFIG_PATH = "config/modules/session_keeper.json"
T = TypeVar("T")


def build_business_session_incident(exc, *, trigger_source, flow_run_id):
    if isinstance(exc, SessionLoginError):
        category = "authentication"
        errors = exc.errors
        attempt_count = exc.attempt_count
    elif isinstance(exc, SessionInfrastructureError):
        category = "infrastructure"
        errors = [str(exc)]
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


def run_with_business_session_reporting(
    operation: Callable[[], T],
    *,
    trigger_source: str,
    reporter=report_business_session_failure,
) -> T:
    try:
        return operation()
    except (SessionLoginError, SessionInfrastructureError) as exc:
        reporter(exc, trigger_source=trigger_source)
        raise
