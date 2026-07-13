"""Resolve shared runtime paths independently from the project checkout."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = Path(r"C:\AutoNotifyRuntime")
_FLOW_AREAS = {"output", "backup", "debug", "tmp"}


def runtime_root(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("AUTO_NOTIFY_RUNTIME_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_RUNTIME_ROOT.resolve()


def runtime_path(relative: str | Path) -> Path:
    return (runtime_root() / Path(relative)).resolve()


def validate_runtime_path(value: str | Path) -> Path:
    """Validate a logical runtime path against the post-migration layout."""
    raw = str(value).replace("\\", "/")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"运行时路径必须是受控的逻辑路径: {value}")
    parts = path.parts
    if len(parts) < 2 or parts[0].lower() != "runtime":
        raise ValueError(f"运行时路径必须以 runtime/ 开头: {value}")
    if len(parts) >= 2 and parts[:2] == ("runtime", "session"):
        return Path(*parts)
    if len(parts) >= 4 and parts[:3] == ("runtime", "modules", parts[2]) and parts[3] == "output":
        return Path(*parts)
    if len(parts) >= 4 and parts[:2] == ("runtime", "flow") and parts[3] in _FLOW_AREAS:
        return Path(*parts)
    raise ValueError(
        "不允许旧或未分类的运行时路径: "
        f"{value}；仅允许 runtime/session/、"
        "runtime/modules/<module>/output/、"
        "runtime/flow/<task>/{output,backup,debug,tmp}/"
    )


def resolve_runtime_path(
    value: str | Path | None, *, project_dir: Path = PROJECT_DIR
) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.parts and path.parts[0].lower() == "runtime":
        logical_path = validate_runtime_path(value)
        return (runtime_root() / Path(*logical_path.parts[1:])).resolve()
    if path.is_absolute():
        return path.resolve()
    if ".." in path.parts:
        raise ValueError(f"不允许父目录路径: {value}")
    return (Path(project_dir) / path).resolve()
