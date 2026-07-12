"""Resolve shared runtime paths independently from the project checkout."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


PROJECT_DIR = Path(__file__).resolve().parents[1]


def runtime_root(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("AUTO_NOTIFY_RUNTIME_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (PROJECT_DIR / "runtime").resolve()


def runtime_path(relative: str | Path) -> Path:
    return (runtime_root() / Path(relative)).resolve()


def resolve_runtime_path(
    value: str | Path | None, *, project_dir: Path = PROJECT_DIR
) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    if path.parts and path.parts[0].lower() == "runtime":
        return (runtime_root() / Path(*path.parts[1:])).resolve()
    return (Path(project_dir) / path).resolve()
