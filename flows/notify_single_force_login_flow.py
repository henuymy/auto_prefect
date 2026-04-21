"""Prefect flow that always refreshes login before running the real pipeline."""

from __future__ import annotations

from pathlib import Path

from prefect import flow

from flows.notify_single_flow import auto_notify_flow


DEFAULT_CONFIG_PATH = Path("config/tasks/local_force_login.json")


@flow(name="auto-notify-force-login-flow")
def auto_notify_force_login_flow(config_path: str | None = None):
    return auto_notify_flow(str(config_path or DEFAULT_CONFIG_PATH))


if __name__ == "__main__":
    auto_notify_force_login_flow()
