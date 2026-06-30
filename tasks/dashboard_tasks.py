"""Prefect task wrappers for the dashboard pipeline."""

from __future__ import annotations

from prefect import get_run_logger, task

from services.dashboard_pipeline import (
    execute_dashboard_daily_pipeline,
    execute_dashboard_monthly_pipeline,
    execute_dashboard_pipeline,
)
from services.dashboard_indicator_sync import execute_dashboard_indicator_sync
from services.dashboard_trigger import load_dashboard_config
from services.dashboard_v2_pipeline import execute_dashboard_v2_pipeline
from services.dashboard_v2_indicator_sync import execute_dashboard_v2_indicator_sync


@task(name="dashboard-run-metrics")
def run_dashboard_metric_task(
    mode: str,
    config_path: str,
    trigger_type: str,
    force_refresh: bool = False,
):
    dashboard_config, _ = load_dashboard_config(config_path)
    schema_version = int(dashboard_config.get("schema_version", 1) or 1)
    if schema_version == 2:
        normalized_mode = str(mode or "").strip().upper()
        if normalized_mode == "INDICATOR_SYNC":
            return execute_dashboard_v2_indicator_sync(
                config_path=config_path,
                trigger_type=trigger_type,
                force_refresh=force_refresh,
                event_logger=get_run_logger(),
            )
        return execute_dashboard_v2_pipeline(
            config_path=config_path,
            trigger_type=trigger_type,
            force_refresh=force_refresh,
            period_type=normalized_mode,
            event_logger=get_run_logger(),
        )
    if schema_version not in {1, 2}:
        raise ValueError(f"schema_version 只支持 1/2: {schema_version!r}")
    pipelines = {
        "REALTIME": execute_dashboard_pipeline,
        "DAY_ACC": execute_dashboard_daily_pipeline,
        "MONTH": execute_dashboard_monthly_pipeline,
        "INDICATOR_SYNC": execute_dashboard_indicator_sync,
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
