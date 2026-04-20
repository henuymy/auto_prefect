"""Prefect tasks for template rendering and screenshot preparation."""

from __future__ import annotations

from prefect import task

from services.template_service import update_template


@task
def update_template_task(config):
    return update_template(config)
