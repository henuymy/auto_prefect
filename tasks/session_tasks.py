"""Prefect tasks for session preparation."""

from __future__ import annotations

from prefect import get_run_logger, task

from services.session_manager import prepare_session


@task
def prepare_session_task(config, force_refresh=False):
    return prepare_session(config, force_refresh=force_refresh, event_logger=get_run_logger())
