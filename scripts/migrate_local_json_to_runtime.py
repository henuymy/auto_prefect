"""Migrate legacy local JSON overrides into config/runtime.local.json."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from utils.config_loader import validate_runtime_local_overrides  # noqa: E402


LEGACY_MAPPINGS = (
    (Path("config/dashboard/session.local.json"), ("dashboard", "session_overrides")),
)


def legacy_sources(project_root: Path) -> list[tuple[Path, tuple[str, str]]]:
    sources = [(project_root / path, target) for path, target in LEGACY_MAPPINGS]
    sources.extend(
        (path, ("module_overrides", path.stem.removesuffix(".local")))
        for path in sorted((project_root / "config" / "modules").glob("*.local.json"))
    )
    sources.extend(
        (path, ("task_overrides", path.stem.removesuffix(".local")))
        for path in sorted((project_root / "config" / "tasks").glob("*.local.json"))
    )
    return [(path, target) for path, target in sources if path.is_file()]


def read_json_object(path: Path, *, project_root: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        relative = path.relative_to(project_root).as_posix()
        raise ValueError(f"JSON 无法解析: {relative}") from exc
    if not isinstance(payload, dict):
        relative = path.relative_to(project_root).as_posix()
        raise ValueError(f"JSON 配置必须是对象: {relative}")
    return payload


def merge_without_conflicts(existing: dict, incoming: dict, path: tuple[str, ...]) -> tuple[dict, list[str]]:
    merged = copy.deepcopy(existing)
    conflicts: list[str] = []
    for key, incoming_value in incoming.items():
        current_path = (*path, key)
        if key not in merged:
            merged[key] = copy.deepcopy(incoming_value)
        elif isinstance(merged[key], dict) and isinstance(incoming_value, dict):
            merged[key], nested_conflicts = merge_without_conflicts(
                merged[key],
                incoming_value,
                current_path,
            )
            conflicts.extend(nested_conflicts)
        elif merged[key] != incoming_value:
            conflicts.append(".".join(current_path))
    return merged, conflicts


def target_override(payload: dict, target: tuple[str, str]) -> dict:
    section, key = target
    if section == "dashboard":
        dashboard = payload.setdefault("dashboard", {})
        if not isinstance(dashboard, dict):
            raise ValueError("运行配置必须是对象: dashboard")
        override = dashboard.setdefault(key, {})
    else:
        overrides = payload.setdefault(section, {})
        if not isinstance(overrides, dict):
            raise ValueError(f"运行配置必须是对象: {section}")
        override = overrides.setdefault(key, {})
    if not isinstance(override, dict):
        raise ValueError(f"运行配置必须是对象: {'.'.join(target)}")
    return override


def ordered_runtime_payload(payload: dict) -> dict:
    dashboard = payload.get("dashboard")
    if isinstance(dashboard, dict):
        ordered_dashboard = {
            key: copy.deepcopy(dashboard[key])
            for key in ("mysql", "session_overrides")
            if key in dashboard
        }
        ordered_dashboard.update(
            (key, copy.deepcopy(value))
            for key, value in dashboard.items()
            if key not in ordered_dashboard
        )
    else:
        ordered_dashboard = dashboard

    ordered = {
        key: copy.deepcopy(payload[key])
        for key in (
            "runtime",
            "prefect",
            "dashboard",
            "monitor",
            "module_overrides",
            "task_overrides",
        )
        if key in payload
    }
    if "dashboard" in ordered:
        ordered["dashboard"] = ordered_dashboard
    ordered.update(
        (key, copy.deepcopy(value)) for key, value in payload.items() if key not in ordered
    )
    return ordered


def write_json_atomically(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(ordered_runtime_payload(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--remove-legacy", action="store_true")
    args = parser.parse_args()
    if args.remove_legacy and not args.apply:
        parser.error("--remove-legacy 只能与 --apply 一起使用")
    return args


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    sources = legacy_sources(project_root)
    if not args.apply:
        for source, target in sources:
            relative = source.relative_to(project_root).as_posix()
            print(f"PREVIEW {relative} -> {'.'.join(target)}")
        return 0

    runtime_path = project_root / "config" / "runtime.local.json"
    try:
        runtime_payload = read_json_object(runtime_path, project_root=project_root)
        candidate = copy.deepcopy(runtime_payload)
        conflicts: list[str] = []
        for source, target in sources:
            incoming = read_json_object(source, project_root=project_root)
            existing = target_override(candidate, target)
            merged, found_conflicts = merge_without_conflicts(existing, incoming, target)
            if found_conflicts:
                conflicts.extend(found_conflicts)
            else:
                existing.clear()
                existing.update(merged)
        if conflicts:
            for conflict in conflicts:
                print(f"CONFLICT {conflict}")
            return 2
        validate_runtime_local_overrides(candidate, project_root=project_root)
        write_json_atomically(runtime_path, candidate)
        written_payload = read_json_object(runtime_path, project_root=project_root)
        validate_runtime_local_overrides(written_payload, project_root=project_root)
    except (OSError, ValueError) as exc:
        print(f"ERROR {exc}")
        return 2

    for source, target in sources:
        relative = source.relative_to(project_root).as_posix()
        print(f"APPLIED {relative} -> {'.'.join(target)}")
    if args.remove_legacy:
        for source, _ in sources:
            relative = source.relative_to(project_root).as_posix()
            source.unlink()
            print(f"REMOVED {relative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
