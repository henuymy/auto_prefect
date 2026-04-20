"""Prefect tasks for baseline template commits."""

from __future__ import annotations

from prefect import task

from services.commit_service import commit_template


@task
def commit_template_task(config):
    return commit_template(config)
