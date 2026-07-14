"""Prefect flow for maintaining the shared authenticated session."""

from __future__ import annotations

from pathlib import Path

from prefect import flow, get_run_logger

from tasks.session_tasks import prepare_session_task
from utils.config_loader import load_json_with_local_override


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_keeper_config(config_path, base_dir=PROJECT_ROOT):
    keeper_config, resolved = load_json_with_local_override(base_dir / config_path)
    session_config, _ = load_json_with_local_override(
        base_dir / keeper_config["autologin_config_path"]
    )
    session_config["required_stages"] = keeper_config["required_stages"]
    return {"session": session_config}, resolved


def run_prepare_session(config):
    return prepare_session_task.with_options(
        retries=0,
    )(config, force_refresh=False, login_attempts=1)


def run_session_keeper(config_path="config/modules/session_keeper.json"):
    config, _ = load_keeper_config(config_path)
    return run_prepare_session(config["session"])


@flow(name="session-keeper-flow")
def session_keeper_flow(config_path="config/modules/session_keeper.json"):
    get_run_logger().info("检查共享登录会话")
    return run_session_keeper(config_path)
