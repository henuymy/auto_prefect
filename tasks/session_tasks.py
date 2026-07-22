"""Prefect tasks for session preparation."""

from __future__ import annotations

from pathlib import Path

from prefect import get_run_logger, task

from services.session_broker import StageSessionBroker


PROJECT_DIR = Path(__file__).resolve().parents[1]


@task(persist_result=False)
def prepare_session_task(
    config: dict,
    force_refresh: bool = False,
    login_attempts: int | None = None,
) -> dict:
    return StageSessionBroker(base_dir=PROJECT_DIR).ensure(
        config,
        force_refresh=force_refresh,
        event_logger=get_run_logger(),
        login_attempts=login_attempts,
    )
