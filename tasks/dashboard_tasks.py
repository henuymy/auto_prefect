"""Prefect task wrappers for the dashboard pipeline."""

from __future__ import annotations

from prefect import get_run_logger, task

from services.dashboard_v2_trigger import load_dashboard_config
from services.dashboard_v2_pipeline import execute_dashboard_v2_pipeline
from services.dashboard_v2_indicator_sync import execute_dashboard_v2_indicator_sync


@task(name="dashboard-run-metrics")
def run_dashboard_metric_task(
    mode: str,
    config_path: str,
    trigger_type: str,
    force_refresh: bool = False,
):
    load_dashboard_config(config_path)
    normalized_mode = str(mode or "").strip().upper()
    if normalized_mode == "INDICATOR_SYNC":
        return execute_dashboard_v2_indicator_sync(
            config_path=config_path,
            trigger_type=trigger_type,
            force_refresh=force_refresh,
            event_logger=get_run_logger(),
        )
    if normalized_mode not in {"REALTIME", "DAY_ACC", "MONTH"}:
        raise ValueError(
            f"mode 只支持 REALTIME, DAY_ACC, MONTH, INDICATOR_SYNC: {mode!r}"
        )
    return execute_dashboard_v2_pipeline(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        period_type=normalized_mode,
        event_logger=get_run_logger(),
    )
