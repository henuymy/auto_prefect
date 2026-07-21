"""JSON config loading helpers."""

from __future__ import annotations

import copy
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def local_override_path(path):
    resolved = Path(path)
    return resolved.with_name(f"{resolved.stem}.local{resolved.suffix}")


def load_json_with_local_override(path):
    resolved = Path(path).resolve()
    with resolved.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    local_path = local_override_path(resolved)
    if local_path.exists():
        with local_path.open("r", encoding="utf-8") as f:
            payload = deep_merge(payload, json.load(f))
    return payload, resolved


def _read_json_object(path):
    with Path(path).open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 配置必须是对象: {Path(path).name}")
    return payload


def _object_section(payload, key, *, label):
    if key not in payload:
        return {}
    value = payload[key]
    if not isinstance(value, dict):
        raise ValueError(f"运行配置必须是对象: {label}")
    return value


def _validate_override_entries(overrides, *, label, directory, project_root):
    for name, value in overrides.items():
        if not isinstance(name, str) or not name or any(char in name for char in ".\\/"):
            raise ValueError(f"运行配置覆盖键无效: {label}.{name}")
        if not isinstance(value, dict):
            raise ValueError(f"运行配置必须是对象: {label}.{name}")
        target = project_root / "config" / directory / f"{name}.json"
        if not target.is_file():
            raise ValueError(f"运行配置覆盖目标不存在: {label}.{name}")


def validate_runtime_local_overrides(payload, *, project_root):
    if not isinstance(payload, dict):
        raise ValueError("运行配置必须是对象")

    root = Path(project_root).resolve()
    dashboard = _object_section(payload, "dashboard", label="dashboard")
    if "session_overrides" in dashboard:
        session_overrides = _object_section(
            dashboard,
            "session_overrides",
            label="dashboard.session_overrides",
        )
        if not (root / "config" / "dashboard" / "session.json").is_file():
            raise ValueError("运行配置覆盖目标不存在: dashboard.session_overrides")
        if not isinstance(session_overrides, dict):
            raise ValueError("运行配置必须是对象: dashboard.session_overrides")

    module_overrides = _object_section(
        payload,
        "module_overrides",
        label="module_overrides",
    )
    task_overrides = _object_section(
        payload,
        "task_overrides",
        label="task_overrides",
    )
    _validate_override_entries(
        module_overrides,
        label="module_overrides",
        directory="modules",
        project_root=root,
    )
    _validate_override_entries(
        task_overrides,
        label="task_overrides",
        directory="tasks",
        project_root=root,
    )


def runtime_override_for(config_path, *, project_root, runtime_config_path):
    root = Path(project_root).resolve()
    try:
        relative = Path(config_path).resolve().relative_to((root / "config").resolve())
    except ValueError:
        return {}

    if relative == Path("dashboard/session.json"):
        section, key = "dashboard", "session_overrides"
    elif len(relative.parts) == 2 and relative.suffix == ".json":
        section = {"modules": "module_overrides", "tasks": "task_overrides"}.get(
            relative.parts[0]
        )
        key = relative.stem
    else:
        return {}
    if section is None:
        return {}

    runtime_path = Path(runtime_config_path)
    if not runtime_path.is_file():
        return {}
    runtime_payload = _read_json_object(runtime_path)
    validate_runtime_local_overrides(runtime_payload, project_root=root)
    if section == "dashboard":
        return _object_section(
            _object_section(runtime_payload, "dashboard", label="dashboard"),
            key,
            label="dashboard.session_overrides",
        )
    return _object_section(runtime_payload, section, label=section).get(key, {})


def load_json_with_runtime_override(path, *, project_root=None, runtime_config_path=None):
    resolved = Path(path).resolve()
    payload = _read_json_object(resolved)
    root = Path(project_root or PROJECT_ROOT).resolve()
    runtime_path = Path(
        runtime_config_path or root / "config" / "runtime.local.json"
    ).resolve()
    override = runtime_override_for(
        resolved,
        project_root=root,
        runtime_config_path=runtime_path,
    )
    return deep_merge(payload, override), resolved
