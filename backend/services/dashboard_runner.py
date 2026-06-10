"""Submit dashboard flow runs to Prefect."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from backend.services.prefect_runner import get_prefect_api_url


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_NAME = "dashboard-session-flow/dashboard-session"
UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)


def submit_dashboard_collection(force_refresh: bool = False) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "prefect",
        "deployment",
        "run",
        DEPLOYMENT_NAME,
        "--param",
        "trigger_type=MANUAL",
        "--param",
        f"force_refresh={'true' if force_refresh else 'false'}",
    ]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PREFECT_API_URL"] = get_prefect_api_url()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    output = "\n".join(
        part for part in [completed.stdout, completed.stderr] if part
    ).strip()
    if completed.returncode != 0:
        raise RuntimeError(output or "提交驾驶舱 Prefect 运行失败")
    flow_run_match = UUID_PATTERN.search(output)
    return {
        "status": "submitted",
        "deployment": DEPLOYMENT_NAME,
        "flow_run_id": flow_run_match.group(0) if flow_run_match else None,
        "message": "驾驶舱触发与会话任务已提交到 Prefect",
    }
