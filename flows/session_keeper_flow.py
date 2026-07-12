"""Prefect flow for maintaining the shared authenticated session."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from prefect import flow, get_run_logger
from prefect.runtime import flow_run

from services.session_alert_service import (
    notify_session_failure,
    notify_session_recovery,
)
from services.session_manager import SessionInfrastructureError, SessionLoginError
from tasks.session_tasks import prepare_session_task
from utils.config_loader import load_json_with_local_override


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_keeper_config(config_path, base_dir=PROJECT_ROOT):
    keeper_config, resolved = load_json_with_local_override(base_dir / config_path)
    session_config, _ = load_json_with_local_override(
        base_dir / keeper_config["autologin_config_path"]
    )
    alert_config, _ = load_json_with_local_override(
        base_dir / keeper_config["alert_config_path"]
    )
    session_config["required_stages"] = keeper_config["required_stages"]
    session_config["login_max_attempts"] = keeper_config["login_max_attempts"]
    session_config["login_retry_delay_seconds"] = keeper_config[
        "login_retry_delay_seconds"
    ]
    return {
        "session": session_config,
        "alert": {
            "webhook_url": alert_config["wecom"]["webhook_url"],
            "incident_state_path": keeper_config["incident_state_path"],
        },
    }, resolved


def next_quarter_hour(now):
    minute = ((now.minute // 15) + 1) * 15
    if minute == 60:
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return now.replace(minute=minute, second=0, microsecond=0)


def current_flow_run_id():
    return str(flow_run.id or "manual")


def retry_infrastructure_only(_task, _task_run, state):
    try:
        state.result()
    except SessionInfrastructureError:
        return True
    except Exception:
        return False
    return False


def run_prepare_session(config):
    return prepare_session_task.with_options(
        retries=1,
        retry_delay_seconds=60,
        retry_condition_fn=retry_infrastructure_only,
    )(config, force_refresh=False)


def notify_failure(alert_config, incident):
    return notify_session_failure(alert_config, incident)


def notify_recovery(alert_config, recovery):
    return notify_session_recovery(alert_config, recovery)


def run_session_keeper(config_path="config/modules/session_keeper.json"):
    config, _ = load_keeper_config(config_path)
    run_id = current_flow_run_id()
    now = datetime.now().astimezone()
    try:
        result = run_prepare_session(config["session"])
    except SessionLoginError as exc:
        notify_failure(
            config["alert"],
            {
                "incident_key": "authentication:shared-session",
                "trigger_source": "session-keeper",
                "failure_category": "authentication",
                "failed_stages": config["session"]["required_stages"],
                "attempt_count": exc.attempt_count,
                "errors": exc.errors,
                "flow_run_id": run_id,
                "next_scheduled_at": next_quarter_hour(now).isoformat(),
            },
        )
        raise
    except SessionInfrastructureError as exc:
        notify_failure(
            config["alert"],
            {
                "incident_key": "infrastructure:shared-session",
                "trigger_source": "session-keeper",
                "failure_category": "infrastructure",
                "failed_stages": config["session"]["required_stages"],
                "attempt_count": 0,
                "errors": [str(exc)],
                "flow_run_id": run_id,
                "next_scheduled_at": next_quarter_hour(now).isoformat(),
            },
        )
        raise
    notify_recovery(
        config["alert"],
        {
            "incident_key": "authentication:shared-session",
            "flow_run_id": run_id,
        },
    )
    notify_recovery(
        config["alert"],
        {
            "incident_key": "infrastructure:shared-session",
            "flow_run_id": run_id,
        },
    )
    return result


@flow(name="session-keeper-flow")
def session_keeper_flow(config_path="config/modules/session_keeper.json"):
    get_run_logger().info("检查共享登录会话")
    return run_session_keeper(config_path)
