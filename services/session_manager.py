"""Session and Cookie management service."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


def resolve_path(value, base_dir=PROJECT_DIR):
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def load_json(path):
    resolved = Path(path).resolve()
    with resolved.open("r", encoding="utf-8") as f:
        return json.load(f), resolved


def write_json(path, payload):
    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return resolved


def stage_names(cookie_dump):
    return [stage.get("stage") for stage in cookie_dump.get("stages", []) if stage.get("stage")]


def find_stage(cookie_dump, stage_name):
    for stage in cookie_dump.get("stages", []):
        if stage.get("stage") == stage_name:
            return stage
    return None


def cookie_is_expired(cookie, now=None, min_ttl_seconds=0):
    expiry = cookie.get("expiry")
    if not expiry:
        return False
    now = now or datetime.now(timezone.utc).timestamp()
    return float(expiry) <= now + min_ttl_seconds


def stage_has_live_cookies(stage, min_ttl_seconds=0):
    cookies = stage.get("cookies") or []
    if not cookies:
        return False
    return any(not cookie_is_expired(cookie, min_ttl_seconds=min_ttl_seconds) for cookie in cookies)


def cookie_dump_age_seconds(cookie_dump):
    generated_at = cookie_dump.get("generated_at")
    if not generated_at:
        return None
    try:
        ts = datetime.fromisoformat(generated_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except ValueError:
        return None


def validate_cookie_dump(cookie_dump, required_stages=None, min_ttl_seconds=0, max_age_seconds=None):
    required_stages = required_stages or []
    missing = []
    expired_or_empty = []
    for stage_name in required_stages:
        stage = find_stage(cookie_dump, stage_name)
        if not stage:
            missing.append(stage_name)
            continue
        if not stage_has_live_cookies(stage, min_ttl_seconds=min_ttl_seconds):
            expired_or_empty.append(stage_name)

    too_old = False
    if max_age_seconds is not None:
        age = cookie_dump_age_seconds(cookie_dump)
        if age is None or age > max_age_seconds:
            too_old = True

    valid = not missing and not expired_or_empty and not too_old
    return {
        "valid": valid,
        "available_stages": stage_names(cookie_dump),
        "missing_stages": missing,
        "expired_or_empty_stages": expired_or_empty,
        "too_old": too_old,
    }


def sync_cookie_dump(source_path, target_path):
    source = Path(source_path).resolve()
    target = Path(target_path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"登录完成后仍未找到 Cookie 文件: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if source != target:
        shutil.copy2(source, target)
    return target


def run_login_command(command, cwd=PROJECT_DIR, timeout_seconds=None):
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        shell=True,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"登录命令执行失败({completed.returncode}): {command}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return {
        "command": command,
        "returncode": completed.returncode,
    }


def load_cookie_dump_if_exists(path):
    resolved = Path(path).resolve()
    if not resolved.exists():
        return None
    payload, _ = load_json(resolved)
    return payload


def prepare_session(config, base_dir=PROJECT_DIR, force_refresh=False):
    cookie_dump_path = resolve_path(config.get("cookie_dump_path", "runtime/cookies/cookie_dump.json"), base_dir)
    legacy_cookie_dump_path = resolve_path(config.get("legacy_cookie_dump_path"), base_dir)
    required_stages = config.get("required_stages") or []
    min_ttl_seconds = int(config.get("min_ttl_seconds", 0) or 0)
    max_age_seconds = config.get("max_age_seconds")
    if max_age_seconds is not None:
        max_age_seconds = int(max_age_seconds)

    cookie_dump = load_cookie_dump_if_exists(cookie_dump_path)
    if not cookie_dump and legacy_cookie_dump_path and legacy_cookie_dump_path.exists():
        sync_cookie_dump(legacy_cookie_dump_path, cookie_dump_path)
        cookie_dump = load_cookie_dump_if_exists(cookie_dump_path)

    if cookie_dump and not force_refresh:
        validation = validate_cookie_dump(cookie_dump, required_stages, min_ttl_seconds=min_ttl_seconds, max_age_seconds=max_age_seconds)
        if validation["valid"]:
            return {
                "status": "reused",
                "cookie_dump_path": str(cookie_dump_path),
                "validation": validation,
            }
    else:
        validation = {
            "valid": False,
            "available_stages": [],
            "missing_stages": required_stages,
            "expired_or_empty_stages": [],
        }

    if config.get("allow_login", True) is False:
        return {
            "status": "invalid",
            "cookie_dump_path": str(cookie_dump_path),
            "validation": validation,
            "reason": "login_disabled",
        }

    command = config.get("login_command")
    if not command:
        raise ValueError("Cookie 无效且未配置 login_command")

    login_timeout_seconds = config.get("login_timeout_seconds")
    if login_timeout_seconds is not None:
        login_timeout_seconds = int(login_timeout_seconds)
    command_result = run_login_command(command, cwd=base_dir, timeout_seconds=login_timeout_seconds)
    source_path = legacy_cookie_dump_path or cookie_dump_path
    sync_cookie_dump(source_path, cookie_dump_path)
    refreshed_cookie_dump = load_cookie_dump_if_exists(cookie_dump_path)
    refreshed_validation = validate_cookie_dump(
        refreshed_cookie_dump or {},
        required_stages,
        min_ttl_seconds=min_ttl_seconds,
    )
    if not refreshed_validation["valid"]:
        raise RuntimeError(f"登录后 Cookie 仍不可用: {refreshed_validation}")

    return {
        "status": "refreshed",
        "cookie_dump_path": str(cookie_dump_path),
        "validation": refreshed_validation,
        "login": command_result,
    }


def prepare_session_from_config(config_path, base_dir=PROJECT_DIR, force_refresh=False):
    config_path = resolve_path(config_path, base_dir)
    config, resolved = load_json(config_path)
    return prepare_session(config, base_dir=resolved.parent, force_refresh=force_refresh)
