"""Unified Prefect flow for realtime and cumulative dashboard metrics."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prefect import flow, get_run_logger

from tasks.dashboard_tasks import run_dashboard_metric_task


DEFAULT_CONFIG_PATH = "config/dashboard/session.json"


@flow(name="dashboard-metric-flow")
def dashboard_metric_flow(
    mode: str = "REALTIME",
    config_path: str = DEFAULT_CONFIG_PATH,
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
):
    logger = get_run_logger()
    normalized_mode = str(mode or "").strip().upper()
    logger.info(
        "启动驾驶舱指标采集: mode=%s, trigger_type=%s, force_refresh=%s",
        normalized_mode,
        trigger_type,
        force_refresh,
    )
    result = run_dashboard_metric_task(
        mode=normalized_mode,
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
    )
    logger.info(
        "驾驶舱指标采集完成: mode=%s, batch_no=%s, stat_date=%s, nodes=%s",
        normalized_mode,
        result.get("batch_no"),
        result.get("stat_date") or result.get("query_date"),
        result.get("node_count", result.get("area_count")),
    )
    return result


if __name__ == "__main__":
    dashboard_metric_flow()
