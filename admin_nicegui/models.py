"""Form defaults and small helpers for the NiceGUI admin.

This module is intentionally independent from admin/app.py.  It mirrors the
existing JSON shape used by the flow, but does not import any Streamlit code.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any


DOWNLOAD_MODE_LABELS = {
    "file": "原始文件下载",
    "json_to_excel": "JSON 转 Excel",
    "json_drilldown_to_excel": "级联下钻 JSON 转 Excel",
}

DOWNLOAD_MODE_VALUES = {label: value for value, label in DOWNLOAD_MODE_LABELS.items()}

AUTH_PRESETS = ["无", "SSR Cookie", "智慧运营 User-Info", "自定义高级"]
STAGE_OPTIONS = ["report_analysis", "smart_ops", "city_ops", "data_market"]
METHOD_OPTIONS = ["POST", "GET", "PUT", "PATCH", "DELETE"]
BODY_TYPE_OPTIONS = ["form", "json", "raw"]

DEFAULT_EXCEL_COLUMNS = [
    {"field": "__level_name", "header": "层级"},
    {"field": "__parent_area_id", "header": "父级areaId"},
    {"field": "__request_area_id", "header": "请求areaId"},
    {"field": "areaName", "header": "名称"},
    {"field": "areaCode", "header": "编码"},
    {"field": "sgs_ajvwdz", "header": "爱家亲情网(V网版)"},
]


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def parse_json_text(value: str, default: Any) -> Any:
    value = (value or "").strip()
    if not value:
        return copy.deepcopy(default)
    return json.loads(value)


def safe_report_name(value: str) -> str:
    cleaned = "".join("_" if ch in '<>:"/\\|?*\x00\r\n\t' else ch for ch in (value or "")).strip()
    return cleaned or f"新建报表-{datetime.now().strftime('%Y%m%d%H%M%S')}"


def response_mode_label(value: str) -> str:
    return DOWNLOAD_MODE_LABELS.get(value or "file", DOWNLOAD_MODE_LABELS["file"])


def response_mode_value(label_or_value: str) -> str:
    if label_or_value in DOWNLOAD_MODE_LABELS:
        return label_or_value
    return DOWNLOAD_MODE_VALUES.get(label_or_value or "", "file")


def empty_download(stage: str = "report_analysis", response_mode: str = "file") -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": "",
        "stage": stage,
        "auth_preset": "无",
        "method": "POST",
        "url": "",
        "headers": {},
        "body_type": "form",
        "data": {},
        "raw_body": "",
        "response_mode": response_mode,
    }
    apply_response_mode_defaults(item)
    return item


def apply_response_mode_defaults(item: dict[str, Any]) -> dict[str, Any]:
    mode = response_mode_value(item.get("response_mode", "file"))
    item["response_mode"] = mode
    if mode in {"json_to_excel", "json_drilldown_to_excel"}:
        item["body_type"] = "json"
        item.setdefault("excel", {})
        item["excel"].setdefault("data_path", "result.tableData")
        item["excel"].setdefault("sheet_name", "地市作战明细")
        item["excel"].setdefault("columns", copy.deepcopy(DEFAULT_EXCEL_COLUMNS))
    if mode == "json_drilldown_to_excel":
        item.setdefault("drilldown", {})
        item["drilldown"].setdefault("data_path", item.get("excel", {}).get("data_path", "result.tableData"))
        item["drilldown"].setdefault("request_area_field", "areaId")
        item["drilldown"].setdefault("next_area_field", "areaCode")
        item["drilldown"].setdefault("levels", ["区县", "网格", "渠道/门店", "人员"])
        item["drilldown"].setdefault("max_requests", 1000)
    return item


def empty_compare_source(download_name: str = "") -> dict[str, Any]:
    return {
        "download_name": download_name,
        "sheet_mappings": [
            {
                "name": "",
                "new_sheet_name": "",
                "template_sheet_name": "",
                "header_row": 1,
                "ignore_columns": [],
                "key_columns": [],
            }
        ],
    }


def empty_send_item() -> dict[str, Any]:
    return {"type": "image", "sheet": "", "text": {}}


def empty_config() -> dict[str, Any]:
    return {
        "name": "",
        "template_path": "",
        "downloads": [empty_download()],
        "compare_sources": [],
        "send": {
            "webhook_url": "",
            "workbook_name": "",
            "items": [empty_send_item()],
        },
        "template_update": {
            "update_condition": "any_changed",
            "write_sheets": "all_compared",
            "send_when_same": True,
        },
        "wait_for_change": {
            "enabled": False,
            "poll_interval_seconds": 300,
            "max_wait_minutes": 180,
        },
        "deployment": {
            "enabled": False,
            "cron": "",
            "timezone": "Asia/Shanghai",
        },
    }


def normalize_download(item: dict[str, Any] | None) -> dict[str, Any]:
    base = empty_download()
    if item:
        base.update(copy.deepcopy(item))
    if "json" in base and "data" not in base:
        base["data"] = base.pop("json")
    base.setdefault("headers", {})
    base.setdefault("data", {})
    base.setdefault("raw_body", "")
    base.setdefault("headers_from_cookies", {})
    base.setdefault("csrf_headers_from_cookies", {})
    base.setdefault("headers_from_session_storage", {})
    base.setdefault("headers_from_local_storage", {})
    base.setdefault("headers_from_cookie_string", {})
    return apply_response_mode_defaults(base)


def normalize_config(config: dict[str, Any] | None) -> dict[str, Any]:
    normalized = empty_config()
    if config:
        normalized.update(copy.deepcopy(config))
    downloads = normalized.get("downloads") or [normalized.get("download", {})]
    normalized["downloads"] = [normalize_download(item) for item in downloads if item is not None]
    if not normalized["downloads"]:
        normalized["downloads"] = [empty_download()]
    send = normalized.setdefault("send", {})
    send.setdefault("webhook_url", "")
    send.setdefault("workbook_name", normalized.get("name", ""))
    send.setdefault("items", [empty_send_item()])
    normalized.setdefault("compare_sources", [])
    normalized.setdefault("template_update", {})
    normalized.setdefault("wait_for_change", {})
    normalized.setdefault("deployment", {})
    return normalized


def display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)

