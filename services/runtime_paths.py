"""Resolve shared runtime paths independently from the project checkout."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = Path(r"C:\AutoNotifyRuntime")
_FLOW_AREAS = {"output", "backup", "debug", "tmp"}
_CONFIG_AREAS = {"drafts", "versions"}
_OPERATIONAL_AREAS = {"logs", "health", "starter_templates", "temp"}


def _configured_runtime_root(value: str, source: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{source} 必须是绝对路径: {value!r}")
    return path.resolve()


def _runtime_root_from_local_config() -> Path | None:
    config_path = PROJECT_DIR / "config" / "runtime.local.json"
    if not config_path.exists():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"运行配置读取失败: {config_path}") from exc
    configured_root = (config.get("runtime") or {}).get("root")
    if not isinstance(configured_root, str) or not configured_root.strip():
        return None
    return _configured_runtime_root(configured_root, "config/runtime.local.json 中的 runtime.root")


def runtime_root(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("AUTO_NOTIFY_RUNTIME_ROOT")
    if configured:
        return _configured_runtime_root(configured, "AUTO_NOTIFY_RUNTIME_ROOT")
    local_config_root = _runtime_root_from_local_config()
    if local_config_root is not None:
        return local_config_root
    return DEFAULT_RUNTIME_ROOT.resolve()


def validate_runtime_relative_path(value: str | Path) -> Path:
    raw = str(value).replace("\\", "/")
    path = Path(raw)
    if not raw or not path.parts:
        raise ValueError(f"运行根相对路径不能为空: {value}")
    if path.parts and path.parts[0].lower() == "runtime":
        raise ValueError(f"运行根相对路径不得以 runtime/ 开头: {value}")
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"运行根相对路径必须是受控的相对路径: {value}")
    parts = path.parts
    if parts[0] == "session":
        return Path(*parts)
    if len(parts) >= 2 and parts[0] == "config" and parts[1] in _CONFIG_AREAS:
        return Path(*parts)
    if parts[0] in _OPERATIONAL_AREAS:
        return Path(*parts)
    if len(parts) >= 3 and parts[0] == "modules" and parts[2] == "output":
        return Path(*parts)
    if len(parts) >= 3 and parts[0] == "flow" and parts[2] in _FLOW_AREAS:
        return Path(*parts)
    raise ValueError(f"不允许未分类的运行根相对路径: {value}")


def runtime_path(relative: str | Path) -> Path:
    logical_path = validate_runtime_relative_path(relative)
    root = runtime_root()
    resolved = (root / logical_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"运行根相对路径解析后越出运行根: {relative}") from exc
    return resolved


def resolve_runtime_relative_path(value: str | Path) -> Path:
    return runtime_path(value)


def is_runtime_relative_path(value: str | Path | None) -> bool:
    if not value:
        return False
    try:
        validate_runtime_relative_path(value)
    except ValueError:
        return False
    return True


def display_path(value: str | Path, *, project_dir: Path = PROJECT_DIR) -> str:
    """Return a stable project or logical Runtime path for API responses and logs."""
    path = Path(value).resolve()
    try:
        return path.relative_to(runtime_root()).as_posix()
    except ValueError:
        pass
    try:
        return path.relative_to(Path(project_dir).resolve()).as_posix()
    except ValueError:
        return str(path)


