"""Read and write report configuration files for the NiceGUI admin."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import normalize_config, safe_report_name


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_DIR / "config" / "reports"
TASKS_DIR = PROJECT_DIR / "config" / "tasks"
TEMPLATES_DIR = PROJECT_DIR / "templates"
COOKIE_DUMP_PATH = PROJECT_DIR / "runtime" / "cookies" / "cookie_dump.json"


def ensure_dirs() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_DIR.mkdir(parents=True, exist_ok=True)


def list_report_names() -> list[str]:
    ensure_dirs()
    return sorted(path.stem for path in REPORTS_DIR.glob("*.json"))


def report_path(name: str) -> Path:
    return REPORTS_DIR / f"{safe_report_name(name)}.json"


def task_path(name: str) -> Path:
    return TASKS_DIR / f"{safe_report_name(name)}.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def load_report(name: str) -> dict[str, Any]:
    path = report_path(name)
    if not path.exists():
        raise FileNotFoundError(f"配置不存在: {path}")
    return normalize_config(load_json(path))


def save_report(config: dict[str, Any]) -> Path:
    name = config.get("name") or "新建报表"
    path = report_path(name)
    write_json(path, config)
    return path


def save_draft(config: dict[str, Any]) -> Path:
    name = f"{config.get('name') or '新建报表'}_draft"
    path = report_path(name)
    write_json(path, config)
    return path


def save_task_config(config: dict[str, Any]) -> Path:
    path = task_path(config.get("name") or "新建报表")
    write_json(path, config)
    return path


def list_templates() -> list[str]:
    if not TEMPLATES_DIR.exists():
        return []
    result = []
    for path in sorted(TEMPLATES_DIR.glob("*.xlsx")):
        result.append(path.relative_to(PROJECT_DIR).as_posix())
    return result


def load_stage_titles() -> dict[str, str]:
    if not COOKIE_DUMP_PATH.exists():
        return {}
    try:
        dump = load_json(COOKIE_DUMP_PATH)
    except Exception:
        return {}
    stages = dump.get("stages") if isinstance(dump, dict) else None
    if not isinstance(stages, dict):
        return {}
    return {
        stage: (value or {}).get("title", "")
        for stage, value in stages.items()
        if isinstance(value, dict)
    }

