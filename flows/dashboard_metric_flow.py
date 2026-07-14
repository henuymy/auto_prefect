"""Unified Prefect flow for realtime and cumulative dashboard metrics."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prefect import flow, get_run_logger

from services.session_retry_service import is_session_expired_error
from services.business_run_alert_service import report_current_business_run_failure
from tasks.dashboard_tasks import run_dashboard_metric_task


DEFAULT_CONFIG_PATH = "config/dashboard/session.json"


def run_dashboard_metric_with_session_refresh(
    *,
    mode,
    config_path,
    trigger_type,
    force_refresh,
):
    parameters = {
        "mode": mode,
        "config_path": config_path,
        "trigger_type": trigger_type,
        "force_refresh": force_refresh,
    }
    def run_collection():
        try:
            return run_dashboard_metric_task(**parameters)
        except RuntimeError as exc:
            if force_refresh or not is_session_expired_error(exc):
                raise
            return run_dashboard_metric_task(**{**parameters, "force_refresh": True})

    return run_collection()


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
    try:
        result = run_dashboard_metric_with_session_refresh(
            mode=normalized_mode,
            config_path=config_path,
            trigger_type=trigger_type,
            force_refresh=force_refresh,
        )
    except Exception as exc:
        report_current_business_run_failure(
            exc,
            workload_type="dashboard",
            workload_id=f"{normalized_mode}:{Path(config_path).stem}",
            flow_name="dashboard-metric-flow",
            failed_stage="驾驶舱指标采集",
        )
        raise
    logger.info(
        "驾驶舱指标采集完成: mode=%s, batch_no=%s, stat_date=%s, "
        "nodes=%s, requests=%s, rows=%s, attempts=%s, total=%ss",
        normalized_mode,
        result.get("batch_no"),
        result.get("stat_date") or result.get("query_date"),
        result.get("node_count", result.get("area_count")),
        result.get("request_count"),
        result.get("row_count"),
        result.get("attempts", 1),
        (result.get("timing") or {}).get("total_seconds", "--"),
    )
    return result


if __name__ == "__main__":
    dashboard_metric_flow()
