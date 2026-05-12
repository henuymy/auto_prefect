"""Prefect deployment helpers for the NiceGUI admin."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


WORK_POOL = "default-agent-pool"
FLOW_ENTRYPOINT = "flows/notify_single_flow.py:auto_notify_flow"


def deploy_report(project_dir: Path, report_name: str, task_cfg_path: Path, cron: str = "", timezone: str = "") -> tuple[bool, str]:
    slug = report_name.replace(" ", "_")
    command = [
        sys.executable,
        "-m",
        "prefect",
        "deploy",
        FLOW_ENTRYPOINT,
        "--name",
        f"notify-{slug}",
        "--pool",
        WORK_POOL,
        "--param",
        f"config_path={task_cfg_path.as_posix()}",
    ]
    if cron:
        command.extend(["--cron", cron])
    if timezone:
        command.extend(["--timezone", timezone])

    completed = subprocess.run(
        command,
        cwd=project_dir,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    return completed.returncode == 0, output

