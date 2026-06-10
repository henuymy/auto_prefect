"""First dashboard phase: trigger, batch registration, lock, and session."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prefect import flow, get_run_logger

from tasks.dashboard_tasks import prepare_dashboard_session_task


DEFAULT_CONFIG_PATH = "config/dashboard/session.json"


@flow(name="dashboard-session-flow")
def dashboard_session_flow(
    config_path: str = DEFAULT_CONFIG_PATH,
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
):
    logger = get_run_logger()
    logger.info(
        "启动驾驶舱触发与会话阶段: trigger_type=%s, force_refresh=%s",
        trigger_type,
        force_refresh,
    )
    result = prepare_dashboard_session_task(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
    )
    logger.info(
        "驾驶舱会话已就绪: batch_no=%s, session_status=%s",
        result.get("batch_no"),
        result.get("session_status"),
    )
    return result


if __name__ == "__main__":
    dashboard_session_flow()
