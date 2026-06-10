"""Prefect task wrappers for the dashboard pipeline."""

from __future__ import annotations

from prefect import get_run_logger, task

from services.dashboard_trigger import execute_session_phase


@task(name="dashboard-prepare-session")
def prepare_dashboard_session_task(
    config_path: str,
    trigger_type: str,
    force_refresh: bool = False,
):
    return execute_session_phase(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        event_logger=get_run_logger(),
    )
