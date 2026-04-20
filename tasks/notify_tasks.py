"""Prefect tasks for notification sending."""

from __future__ import annotations

from prefect import task

from services.notify_service import send_notification_package
from services.screenshot_service import build_message_package


@task
def build_message_package_task(config):
    return build_message_package(config)


@task(retries=2, retry_delay_seconds=120)
def send_notification_package_task(config, package, package_file=None, dry_run=False, timeout=30):
    return send_notification_package(
        config,
        package,
        package_file=package_file,
        dry_run=dry_run,
        timeout=timeout,
    )
