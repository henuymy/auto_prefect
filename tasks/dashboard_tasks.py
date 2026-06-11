"""Prefect task wrappers for the dashboard pipeline."""

from __future__ import annotations

from prefect import get_run_logger, task

from services.dashboard_acc_pipeline import (
    execute_dashboard_daily_pipeline,
    execute_dashboard_monthly_pipeline,
)
from services.dashboard_pipeline import execute_dashboard_pipeline


@task(name="dashboard-run-metrics")
def run_dashboard_metric_task(
    mode: str,
    config_path: str,
    trigger_type: str,
    force_refresh: bool = False,
):
    pipelines = {
        "REALTIME": execute_dashboard_pipeline,
        "DAY_ACC": execute_dashboard_daily_pipeline,
        "MONTH": execute_dashboard_monthly_pipeline,
    }
    normalized_mode = str(mode or "").strip().upper()
    pipeline = pipelines.get(normalized_mode)
    if pipeline is None:
        raise ValueError(
            f"mode 只支持 {', '.join(pipelines)}: {mode!r}"
        )
    return pipeline(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        event_logger=get_run_logger(),
    )
