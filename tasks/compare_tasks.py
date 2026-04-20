"""Prefect tasks for data comparison."""

from __future__ import annotations

from prefect import task

from services.compare_service import compare_report


@task
def compare_report_task(config):
    return compare_report(config)
