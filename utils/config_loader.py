"""JSON config loading helpers."""

from __future__ import annotations

import copy
import json
from pathlib import Path


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
