from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = PROJECT_ROOT / "config" / "reports"
TASKS_DIR = PROJECT_ROOT / "config" / "tasks"
DRAFTS_DIR = PROJECT_ROOT / "runtime" / "drafts"
VERSIONS_DIR = PROJECT_ROOT / "runtime" / "config_versions"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_name(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value).strip()
    return cleaned or "未命名配置"


def _mtime_text(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def normalize_config(data: dict[str, Any], config_id: str | None = None, updated_at: str | None = None) -> dict[str, Any]:
    downloads = data.get("downloads")
    if not downloads:
        single_download = data.get("download")
        downloads = [single_download] if isinstance(single_download, dict) else []
    normalized_downloads = []
    for index, item in enumerate(downloads or [], start=1):
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        next_item.pop("csrf_headers_from_cookies", None)
        next_item["name"] = next_item.get("name") or f"抓取项-{index}"
        next_item["stage"] = next_item.get("stage") or "report_analysis"
        if next_item.get("method"):
            next_item["method"] = str(next_item["method"]).upper()
        next_item["headers"] = next_item.get("headers") or {}
        if "data" not in next_item and isinstance(next_item.get("json"), dict):
            next_item["data"] = next_item["json"]
        if "data" not in next_item and next_item.get("body_type") != "raw":
            next_item["data"] = {}
        normalized_downloads.append(next_item)
    downloads = normalized_downloads

    compare_sources = data.get("compare_sources")
    if not compare_sources:
        compare = data.get("compare") if isinstance(data.get("compare"), dict) else {}
        sheet_mappings = compare.get("sheet_mappings") or []
        compare_sources = []
        if sheet_mappings:
            compare_sources = [{"download_name": downloads[0].get("name", "") if downloads else "", "sheet_mappings": sheet_mappings}]

    send = data.get("send") if isinstance(data.get("send"), dict) else {}
    template_update = data.get("template_update") if isinstance(data.get("template_update"), dict) else {}
    wait_for_change = data.get("wait_for_change") if isinstance(data.get("wait_for_change"), dict) else {}
    deployment = data.get("deployment") if isinstance(data.get("deployment"), dict) else {}

    normalized = dict(data)
    # The React admin edits the modern multi-download shape. Keep legacy input
    # readable, but never write legacy single-section keys back to disk.
    for legacy_key in ("download", "env", "compare"):
        normalized.pop(legacy_key, None)
    normalized.update(
        {
            "id": config_id or data.get("id") or _safe_name(str(data.get("name") or "未命名配置")),
            "name": data.get("name") or config_id or "未命名配置",
            "template_path": data.get("template_path") or "",
            "enabled": data.get("enabled", True),
            "description": data.get("description") or "",
            "downloads": downloads,
            "compare_sources": compare_sources,
            "send": {
                "webhook_url": send.get("webhook_url", ""),
                "workbook_name": send.get("workbook_name") or data.get("name") or "",
                "items": send.get("items") or [],
            },
            "template_update": {
                "update_condition": template_update.get("update_condition", "any_changed"),
                "write_sheets": template_update.get("write_sheets", "all_compared"),
                "send_when_same": template_update.get("send_when_same", True),
            },
            "wait_for_change": {
                "enabled": wait_for_change.get("enabled", False),
                "poll_interval_seconds": wait_for_change.get("poll_interval_seconds", 300),
                "max_wait_minutes": wait_for_change.get("max_wait_minutes", 180),
            },
            "deployment": {
                "enabled": deployment.get("enabled", False),
                "cron": deployment.get("cron", ""),
                "timezone": deployment.get("timezone", "Asia/Shanghai"),
            },
            "updatedAt": updated_at or data.get("updatedAt"),
        }
    )
    return normalized


def list_configs() -> list[dict[str, Any]]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    configs: list[dict[str, Any]] = []
    for path in sorted(REPORTS_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            config = normalize_config(_read_json(path), config_id=path.stem, updated_at=_mtime_text(path))
            configs.append(config)
        except Exception as exc:  # Keep a broken file visible instead of hiding it.
            configs.append(
                {
                    "id": path.stem,
                    "name": path.stem,
                    "template_path": "",
                    "enabled": False,
                    "description": f"读取失败: {exc}",
                    "downloads": [],
                    "compare_sources": [],
                    "send": {"webhook_url": "", "workbook_name": path.stem, "items": []},
                    "template_update": {"update_condition": "any_changed", "write_sheets": "all_compared", "send_when_same": True},
                    "wait_for_change": {"enabled": False, "poll_interval_seconds": 300, "max_wait_minutes": 180},
                    "deployment": {"enabled": False, "cron": "", "timezone": "Asia/Shanghai"},
                    "lastRun": "failed",
                    "updatedAt": _mtime_text(path),
                }
            )
    return configs


def get_config(config_id: str) -> dict[str, Any]:
    path = REPORTS_DIR / f"{_safe_name(config_id)}.json"
    if not path.exists():
        raise FileNotFoundError(f"配置不存在: {config_id}")
    return normalize_config(_read_json(path), config_id=path.stem, updated_at=_mtime_text(path))


def save_config(config_id: str, config: dict[str, Any]) -> dict[str, Any]:
    name = _safe_name(config.get("name") or config_id)
    path = REPORTS_DIR / f"{name}.json"
    normalized = normalize_config(config, config_id=name)
    if path.exists():
        version_dir = VERSIONS_DIR / name
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _write_json(version_dir / f"{timestamp}.json", _read_json(path))
    _write_json(path, normalized)
    if config_id != name:
        old_path = REPORTS_DIR / f"{_safe_name(config_id)}.json"
        if old_path.exists() and old_path != path:
            old_path.unlink()
    return normalize_config(_read_json(path), config_id=path.stem, updated_at=_mtime_text(path))


def list_versions(config_id: str) -> list[dict[str, Any]]:
    name = _safe_name(config_id)
    version_dir = VERSIONS_DIR / name
    if not version_dir.exists():
        return []
    versions = []
    for path in sorted(version_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        versions.append(
            {
                "id": path.stem,
                "path": str(path.relative_to(PROJECT_ROOT)),
                "size": path.stat().st_size,
                "createdAt": _mtime_text(path),
            }
        )
    return versions


def get_version(config_id: str, version_id: str) -> dict[str, Any]:
    path = VERSIONS_DIR / _safe_name(config_id) / f"{_safe_name(version_id)}.json"
    if not path.exists():
        raise FileNotFoundError(f"配置版本不存在: {config_id}/{version_id}")
    return normalize_config(_read_json(path), config_id=config_id, updated_at=_mtime_text(path))


def restore_version(config_id: str, version_id: str) -> dict[str, Any]:
    version = get_version(config_id, version_id)
    version["name"] = config_id
    return save_config(config_id, version)


def save_draft(config_id: str, config: dict[str, Any]) -> dict[str, Any]:
    name = _safe_name(config.get("name") or config_id)
    path = DRAFTS_DIR / f"{name}.json"
    normalized = normalize_config(config, config_id=name)
    _write_json(path, normalized)
    return normalize_config(_read_json(path), config_id=path.stem, updated_at=_mtime_text(path))


def create_config(config: dict[str, Any]) -> dict[str, Any]:
    return save_config(_safe_name(config.get("name") or "新建通报配置"), config)


def delete_config(config_id: str) -> list[str]:
    name = _safe_name(config_id)
    report_path = REPORTS_DIR / f"{name}.json"
    if not report_path.exists():
        raise FileNotFoundError(f"配置不存在: {config_id}")

    candidates = [
        report_path,
        TASKS_DIR / f"{name}.json",
        DRAFTS_DIR / f"{name}.json",
        DRAFTS_DIR / f"{name}.dry_run.task.json",
        DRAFTS_DIR / f"{name}.real_test.task.json",
    ]
    deleted: list[str] = []
    for path in candidates:
        if path.exists() and path.is_file():
            path.unlink()
            deleted.append(str(path.relative_to(PROJECT_ROOT)))
    return deleted
