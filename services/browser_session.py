"""Manage the retained Edge session used by auto login."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SESSION_STATE_PATH = PROJECT_DIR / "runtime" / "browser_session" / "session.json"
DEFAULT_USER_DATA_DIR = PROJECT_DIR / "runtime" / "browser_session" / "edge_profile_auto_login"


def resolve_path(value, base_dir=PROJECT_DIR):
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else (base_dir / path).resolve()


def browser_config(config: dict, base_dir=PROJECT_DIR):
    browser = config.get("browser") or {}
    return {
        "keep_open_after_login": bool(browser.get("keep_open_after_login", False)),
        "session_state_path": resolve_path(browser.get("session_state_path"), base_dir) or DEFAULT_SESSION_STATE_PATH,
        "user_data_dir": resolve_path(browser.get("user_data_dir"), base_dir) or DEFAULT_USER_DATA_DIR,
    }


def read_session_state(session_state_path=DEFAULT_SESSION_STATE_PATH):
    try:
        return json.loads(Path(session_state_path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_session_state(session_state_path, payload):
    path = Path(session_state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def remove_session_state(session_state_path=DEFAULT_SESSION_STATE_PATH):
    try:
        Path(session_state_path).unlink()
    except FileNotFoundError:
        pass


def record_browser_session(session_state_path, user_data_dir, driver_pid=None, config_path=None):
    browser_pids = find_edge_pids_by_user_data_dir(user_data_dir)
    return write_session_state(
        session_state_path,
        {
            "created_at": datetime.now().isoformat(),
            "driver_pid": driver_pid,
            "browser_pids": browser_pids,
            "user_data_dir": str(Path(user_data_dir).resolve()),
            "config_path": str(config_path) if config_path else None,
        },
    )


def find_edge_pids_by_user_data_dir(user_data_dir):
    if os.name != "nt":
        return []
    profile = str(Path(user_data_dir).resolve())
    script = r"""
$needle = $env:AUTO_NOTIFY_EDGE_PROFILE
$items = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {
    $_.Name -in @('msedge.exe', 'msedgedriver.exe') -and
    $_.CommandLine -and
    $_.CommandLine.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
  } |
  Select-Object -ExpandProperty ProcessId
$items | ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        env={**os.environ, "AUTO_NOTIFY_EDGE_PROFILE": profile},
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, int):
        return [payload]
    if isinstance(payload, list):
        return [int(item) for item in payload if str(item).isdigit()]
    return []


def stop_process_ids(process_ids):
    stopped = []
    for process_id in sorted({int(pid) for pid in process_ids if pid}):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process_id), "/T", "/F"],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            stopped.append(process_id)
        except Exception:
            continue
    return stopped


def close_recorded_browser_session(session_state_path=DEFAULT_SESSION_STATE_PATH):
    state_path = Path(session_state_path)
    state = read_session_state(state_path)
    if not state:
        remove_session_state(state_path)
        return {"status": "missing", "stopped_pids": []}

    process_ids = []
    if state.get("driver_pid"):
        process_ids.append(state.get("driver_pid"))
    if state.get("user_data_dir"):
        process_ids.extend(find_edge_pids_by_user_data_dir(state["user_data_dir"]))
    process_ids.extend(state.get("browser_pids") or [])

    stopped = stop_process_ids(process_ids)
    remove_session_state(state_path)
    return {"status": "closed", "stopped_pids": stopped, "state_path": str(state_path)}


def close_browser_session(session_state_path=DEFAULT_SESSION_STATE_PATH, user_data_dir=None, wait_seconds=10, poll_seconds=0.5):
    state_path = Path(session_state_path)
    state = read_session_state(state_path)
    profile_dir = user_data_dir or state.get("user_data_dir") or DEFAULT_USER_DATA_DIR
    process_ids = []
    if state.get("driver_pid"):
        process_ids.append(state.get("driver_pid"))
    process_ids.extend(state.get("browser_pids") or [])
    if profile_dir:
        process_ids.extend(find_edge_pids_by_user_data_dir(profile_dir))

    stopped = stop_process_ids(process_ids)
    deadline = time.time() + float(wait_seconds or 0)
    remaining = find_edge_pids_by_user_data_dir(profile_dir) if profile_dir else []
    while remaining and time.time() < deadline:
        time.sleep(float(poll_seconds or 0.5))
        remaining = find_edge_pids_by_user_data_dir(profile_dir)
        if remaining:
            stopped.extend(stop_process_ids(remaining))

    remove_session_state(state_path)
    return {
        "status": "closed",
        "stopped_pids": sorted({int(pid) for pid in stopped if pid}),
        "remaining_pids": remaining,
        "state_path": str(state_path),
        "user_data_dir": str(Path(profile_dir).resolve()) if profile_dir else None,
    }
