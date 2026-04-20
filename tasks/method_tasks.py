"""Prefect tasks for report method execution."""

from __future__ import annotations

from prefect import task

from services.method_service import download_reports


@task(retries=2, retry_delay_seconds=120)
def download_reports_task(config, dry_run=False, debug=False):
    return download_reports(config, dry_run=dry_run, debug=debug)
