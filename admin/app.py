"""Streamlit admin UI for managing report configs and Prefect deployments."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import streamlit as st

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_DIR / "config" / "reports"
TASKS_DIR = PROJECT_DIR / "config" / "tasks"
TEMPLATES_DIR = PROJECT_DIR / "templates"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
TASKS_DIR.mkdir(parents=True, exist_ok=True)

WORK_POOL = "default-agent-pool"
FLOW_ENTRYPOINT = "flows/notify_single_flow.py:auto_notify_flow"
DEFAULT_STAGE_OPTIONS = ["report_analysis", "smart_ops", "city_ops", "data_market"]
COOKIE_DUMP_PATH = PROJECT_DIR / "runtime" / "cookies" / "cookie_dump.json"
SAME_ACTION_OPTIONS = ["等待数据变化后发送", "直接发送当前通报", "直接结束"]
DOWNLOAD_MODE_FILE = "原始文件下载"
DOWNLOAD_MODE_JSON = "JSON转Excel"
DOWNLOAD_MODE_DRILLDOWN = "级联下钻JSON转Excel"
DOWNLOAD_MODE_OPTIONS = [DOWNLOAD_MODE_FILE, DOWNLOAD_MODE_JSON, DOWNLOAD_MODE_DRILLDOWN]

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from utils.request_parser import (  # noqa: E402
    headers_from_header_rows,
    parse_request_by_mode,
)


LEGACY_SSR_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}
LEGACY_SSR_HEADERS_FROM_COOKIES = {"ssr-token": "ssr-token"}
LEGACY_SSR_CSRF_HEADERS_FROM_COOKIES = {"ssr-header": "ssr-token"}
AUTH_PRESET_NONE = "无"
AUTH_PRESET_SSR = "SSR Cookie"
AUTH_PRESET_SMART_OPS = "智慧运营 User-Info"
AUTH_PRESET_CUSTOM = "自定义高级"
AUTH_PRESET_OPTIONS = [
    AUTH_PRESET_NONE,
    AUTH_PRESET_SSR,
    AUTH_PRESET_SMART_OPS,
    AUTH_PRESET_CUSTOM,
]
BODY_PLACEHOLDER_OPTIONS = [
    "今天 YYYY-MM-DD",
    "今天 YYYYMMDD",
    "昨天 YYYY-MM-DD",
    "昨天 YYYYMMDD",
    "前天 YYYY-MM-DD",
    "前天 YYYYMMDD",
    "当前小时",
    "当前小时两位",
    "sessionStorage",
    "localStorage",
]
BODY_PLACEHOLDER_MAP = {
    "今天 YYYY-MM-DD": "${today}",
    "今天 YYYYMMDD": "${today_yyyymmdd}",
    "昨天 YYYY-MM-DD": "${yesterday}",
    "昨天 YYYYMMDD": "${yesterday_yyyymmdd}",
    "前天 YYYY-MM-DD": "${day_before_yesterday}",
    "前天 YYYYMMDD": "${day_before_yesterday_yyyymmdd}",
    "当前小时": "${hour}",
    "当前小时两位": "${hour2}",
}


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_reports() -> list[Path]:
    return sorted(REPORTS_DIR.glob("*.json"))


def report_name_from_path(path: Path) -> str:
    return path.stem


def task_config_path(report_name: str) -> Path:
    return TASKS_DIR / f"{report_name}.json"


def normalize_project_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR)).replace("\\", "/")
    except ValueError:
        return str(path)


def list_template_files() -> list[str]:
    if not TEMPLATES_DIR.exists():
        return []
    extensions = {".xlsx", ".xlsm", ".xls"}
    return [
        normalize_project_path(path)
        for path in sorted(TEMPLATES_DIR.rglob("*"))
        if path.is_file() and path.suffix.lower() in extensions
    ]


def build_task_config(report_name: str, report_config_path: str, download_name: str | None = None) -> dict:
    slug = report_name.replace(" ", "_")
    return {
        "flow_name": f"auto-notify-{slug}",
        "runtime_dir": f"runtime/flow/{slug}",
        "report_config_path": report_config_path,
        "wait_for_change": {
            "enabled": True,
            "poll_interval_seconds": 300,
            "max_wait_minutes": 180,
        },
        "steps": {
            "login": {
                "enabled": True,
                "config_path": "config/modules/autologin.json",
                "force_refresh": False,
            },
            "download": {
                "enabled": True,
                "config_path": "config/modules/report_downloader.json",
                "dry_run": False,
                "debug": False,
            },
            "compare": {
                "enabled": True,
                "config_path": "config/modules/report_compare.json",
                "generated_config_path": f"runtime/flow/{slug}/compare_config.json",
                "download_report_name": download_name or report_name,
            },
            "update_template": {
                "enabled": True,
                "config_path": "config/modules/template_updater.json",
                "generated_config_path": f"runtime/flow/{slug}/template_updater_config.json",
                "output_dir": f"runtime/flow/{slug}/templates",
                "manifest_path": f"runtime/flow/{slug}/update_manifest.json",
            },
            "send_wecom": {
                "enabled": True,
                "base_config_path": "config/modules/wecom_sender.json",
                "generated_config_path": f"runtime/flow/{slug}/excel_sender_config.json",
                "runtime_dir": f"runtime/flow/{slug}/wecom",
                "dry_run": False,
                "timeout": 30,
            },
            "commit_template": {
                "enabled": True,
                "config_path": "config/modules/template_commit.json",
                "update_manifest_path": f"runtime/flow/{slug}/update_manifest.json",
                "send_result_path": f"runtime/flow/{slug}/wecom/send_result.json",
                "backup_dir": f"runtime/flow/{slug}/backups",
                "manifest_path": f"runtime/flow/{slug}/commit_manifest.json",
            },
        },
    }


def deploy(report_name: str, task_cfg_path: Path, cron: str | None, timezone: str | None = None):
    slug = report_name.replace(" ", "_")
    deployment_name = f"notify-{slug}"
    cmd = [
        sys.executable, "-m", "prefect", "deploy",
        "--name", deployment_name,
        "--entrypoint", FLOW_ENTRYPOINT,
        "--pool", WORK_POOL,
        "--param", f"config_path={task_cfg_path}",
    ]
    if cron:
        cmd += ["--cron", cron]
        if timezone:
            cmd += ["--timezone", timezone]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode == 0, result.stdout + result.stderr


def list_stage_options() -> list[str]:
    login_config_path = PROJECT_DIR / "config" / "modules" / "login_config.json"
    try:
        login_config = load_json(login_config_path)
    except Exception:
        return list(DEFAULT_STAGE_OPTIONS)

    options: list[str] = []
    for app in login_config.get("usm_cookie_apps", []):
        stage = (app or {}).get("stage")
        if isinstance(stage, str) and stage and stage not in options:
            options.append(stage)

    if not options:
        return list(DEFAULT_STAGE_OPTIONS)

    # 兜底保留默认值，避免历史配置缺项时无可选值
    for stage in DEFAULT_STAGE_OPTIONS:
        if stage not in options:
            options.append(stage)
    return options


def load_stage_title_map_from_dump() -> dict[str, str]:
    if not COOKIE_DUMP_PATH.exists():
        return {}
    try:
        payload = load_json(COOKIE_DUMP_PATH)
    except Exception:
        return {}

    stage_title_map: dict[str, str] = {}
    for item in payload.get("stages", []):
        stage = (item or {}).get("stage")
        title = (item or {}).get("title")
        if isinstance(stage, str) and stage and isinstance(title, str) and title.strip():
            stage_title_map[stage] = title.strip()
    return stage_title_map


def _append_payload_value(payload: dict, key: str, value: str):
    if key in payload:
        if isinstance(payload[key], list):
            payload[key].append(value)
        else:
            payload[key] = [payload[key], value]
    else:
        payload[key] = value


def parse_payload_text_to_json(raw_text: str) -> dict:
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return {}

    # 1) 已经是 JSON 对象时直接使用
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 2) 兼容 x-www-form-urlencoded: a=1&b=2
    query_candidate = raw_text[1:] if raw_text.startswith("?") else raw_text
    if "=" in query_candidate and "&" in query_candidate and "\n" not in query_candidate:
        payload = {}
        for key, value in parse_qsl(query_candidate, keep_blank_values=True):
            _append_payload_value(payload, key, value)
        return payload

    # 3) 兼容按行粘贴: key=value 或 key: value
    payload = {}
    for line in raw_text.splitlines():
        line = line.strip().strip(",")
        if not line:
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        key = key.strip().strip('"').strip("'")
        value = value.strip().strip('"').strip("'")
        if key:
            _append_payload_value(payload, key, value)

    if payload:
        return payload
    raise ValueError("无法识别请求载体格式，请粘贴 JSON、a=1&b=2，或按行 key=value")


def merge_url_query_payload(url: str, payload: dict) -> dict:
    merged = dict(payload or {})
    url = (url or "").strip()
    if not url:
        return merged
    parsed_url = urlparse(url)
    for key, value in parse_qsl(parsed_url.query, keep_blank_values=True):
        if key not in merged:
            merged[key] = value
    return merged


def format_json_text_in_state(state_key: str, label: str):
    raw = st.session_state.get(state_key, "")
    try:
        parsed = json.loads(raw or "null")
        st.session_state[state_key] = json.dumps(parsed, ensure_ascii=False, indent=2)
        st.success(f"{label} 已格式化")
    except json.JSONDecodeError as exc:
        st.error(f"{label} 不是合法 JSON，无法格式化: {exc}")


def init_json_editor_state(marker_key: str, state_key: str, selected_marker: str, value):
    if st.session_state.get(marker_key) != selected_marker:
        st.session_state[marker_key] = selected_marker
        st.session_state[state_key] = json.dumps(value, ensure_ascii=False, indent=2)


def init_state_value(marker_key: str, state_key: str, selected_marker: str, value):
    if st.session_state.get(marker_key) != selected_marker:
        st.session_state[marker_key] = selected_marker
        st.session_state[state_key] = value


def same_action_from_config(template_update: dict, wait_cfg: dict) -> str:
    if bool((template_update or {}).get("send_when_same", False)):
        return "直接发送当前通报"
    if bool((wait_cfg or {}).get("enabled", True)):
        return "等待数据变化后发送"
    return "直接结束"


def json_dumps_for_cell(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def parse_json_object(value, default=None):
    default = {} if default is None else default
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return default
    return parsed if isinstance(parsed, dict) else default


def normalize_storage_path(value):
    if isinstance(value, str):
        return value.replace("zhyptInfo.", "zhyyptInfo.")
    return value


def normalize_storage_mapping(mapping: dict) -> dict:
    return {
        key: normalize_storage_path(value)
        for key, value in (mapping or {}).items()
    }


def default_body_type_for_download(item: dict) -> str:
    if item.get("body_type"):
        return item.get("body_type")
    if "json" in item:
        return "json"
    if item.get("raw_body"):
        return "raw"
    return "form"


def is_legacy_ssr_download(item: dict) -> bool:
    if not item:
        return False
    has_real_content = bool(item.get("name") or item.get("url") or item.get("data") or item.get("json"))
    has_new_request_fields = any(
        key in item
        for key in ("method", "body_type", "headers", "headers_from_cookies", "csrf_headers_from_cookies", "raw_body")
    )
    return has_real_content and not has_new_request_fields


def default_headers_for_download(item: dict) -> dict:
    if item.get("headers"):
        return item.get("headers") or {}
    if is_legacy_ssr_download(item):
        return dict(LEGACY_SSR_HEADERS)
    return {}


def default_headers_from_cookies_for_download(item: dict) -> dict:
    if item.get("headers_from_cookies"):
        return item.get("headers_from_cookies") or {}
    if is_legacy_ssr_download(item):
        return dict(LEGACY_SSR_HEADERS_FROM_COOKIES)
    return {}


def default_csrf_headers_from_cookies_for_download(item: dict) -> dict:
    if item.get("csrf_headers_from_cookies"):
        return item.get("csrf_headers_from_cookies") or {}
    if is_legacy_ssr_download(item):
        return dict(LEGACY_SSR_CSRF_HEADERS_FROM_COOKIES)
    return {}


def infer_auth_preset(item: dict) -> str:
    if item.get("auth_preset") in AUTH_PRESET_OPTIONS:
        return item.get("auth_preset")
    session_mapping = normalize_storage_mapping(item.get("headers_from_session_storage") or {})
    cookie_mapping = item.get("headers_from_cookies") or {}
    csrf_mapping = item.get("csrf_headers_from_cookies") or {}
    if session_mapping.get("User-Info") == "zhyyptInfo.accessToken":
        return AUTH_PRESET_SMART_OPS
    if cookie_mapping.get("ssr-token") == "ssr-token" or csrf_mapping.get("ssr-header") == "ssr-token":
        return AUTH_PRESET_SSR
    if session_mapping or cookie_mapping or csrf_mapping or item.get("headers_from_local_storage"):
        return AUTH_PRESET_CUSTOM
    return AUTH_PRESET_NONE


def apply_auth_preset_to_item(item: dict, auth_preset: str) -> dict:
    if auth_preset == AUTH_PRESET_SSR:
        item["headers_from_cookies"] = {"ssr-token": "ssr-token"}
        item["csrf_headers_from_cookies"] = {"ssr-header": "ssr-token"}
        item.pop("headers_from_session_storage", None)
        item.pop("headers_from_local_storage", None)
    elif auth_preset == AUTH_PRESET_SMART_OPS:
        item["headers_from_session_storage"] = {"User-Info": "zhyyptInfo.accessToken"}
        item.pop("headers_from_cookies", None)
        item.pop("csrf_headers_from_cookies", None)
        item.pop("headers_from_local_storage", None)
    elif auth_preset == AUTH_PRESET_NONE:
        item.pop("headers_from_cookies", None)
        item.pop("csrf_headers_from_cookies", None)
        item.pop("headers_from_session_storage", None)
        item.pop("headers_from_local_storage", None)
    return item


def remove_dynamic_headers_from_static(headers: dict, *dynamic_mappings: dict) -> dict:
    if not headers:
        return {}
    dynamic_names = {
        str(header_name).lower()
        for mapping in dynamic_mappings
        for header_name in (mapping or {}).keys()
    }
    return {
        header_name: header_value
        for header_name, header_value in headers.items()
        if str(header_name).lower() not in dynamic_names
    }


def special_header_rows_from_download_row(row: dict) -> list[dict]:
    rows = []
    for source_key, source_label in (
        ("headers_from_cookies_json", "Cookie"),
        ("headers_from_session_storage_json", "sessionStorage"),
        ("headers_from_local_storage_json", "localStorage"),
    ):
        mapping = parse_json_object(row.get(source_key), {})
        if source_label in {"sessionStorage", "localStorage"}:
            mapping = normalize_storage_mapping(mapping)
        for header_name, source_path in mapping.items():
            rows.append({
                "enabled": True,
                "header_name": header_name,
                "source_type": source_label,
                "source_path": source_path,
            })
    if not rows:
        rows = [
            {
                "enabled": True,
                "header_name": "User-Info",
                "source_type": "sessionStorage",
                "source_path": "zhyyptInfo.accessToken",
            }
        ]
    return rows


def storage_placeholder(source_type: str, source_path: str) -> str:
    if source_type in BODY_PLACEHOLDER_MAP:
        return BODY_PLACEHOLDER_MAP[source_type]
    if source_type == "sessionStorage":
        return f"${{session_storage:{source_path}}}"
    if source_type == "localStorage":
        return f"${{local_storage:{source_path}}}"
    return str(source_path or "")


def flatten_dict_paths(payload: dict, prefix: str = "") -> list[tuple[str, object]]:
    paths = []
    for key, value in (payload or {}).items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            paths.extend(flatten_dict_paths(value, path))
        else:
            paths.append((path, value))
    return paths


def source_from_placeholder(value) -> tuple[str, str, bool]:
    if not isinstance(value, str):
        return "今天 YYYYMMDD", "", False
    for source_type, placeholder in BODY_PLACEHOLDER_MAP.items():
        if value == placeholder:
            return source_type, "", True
    if value.startswith("${session_storage:") and value.endswith("}"):
        return "sessionStorage", value[len("${session_storage:"):-1], True
    if value.startswith("${local_storage:") and value.endswith("}"):
        return "localStorage", value[len("${local_storage:"):-1], True
    return "今天 YYYYMMDD", "", False


def body_placeholder_rows_from_download_row(row: dict) -> list[dict]:
    payload = parse_json_object(row.get("data_json"), {})
    rows = []
    for field_path, value in flatten_dict_paths(payload):
        if field_path.lower() in {"querydate", "date", "day", "statdate"} or (
            isinstance(value, str) and value.startswith("${")
        ):
            source_type, source_path, enabled = source_from_placeholder(value)
            rows.append({
                "enabled": enabled,
                "body_field": field_path,
                "source_type": source_type,
                "source_path": source_path,
            })
    if not rows:
        rows = [
            {
                "enabled": False,
                "body_field": "queryDate",
                "source_type": "今天 YYYYMMDD",
                "source_path": "",
            }
        ]
    return rows


def set_nested_dict_value(payload: dict, dotted_path: str, value):
    parts = [part.strip() for part in str(dotted_path or "").split(".") if part.strip()]
    if not parts:
        return payload
    current = payload
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value
    return payload


def infer_download_mode(report_cfg: dict) -> str:
    downloads = report_cfg.get("downloads") or [report_cfg.get("download", {})]
    modes = {(item or {}).get("response_mode", "file") for item in downloads}
    if "json_drilldown_to_excel" in modes:
        return DOWNLOAD_MODE_DRILLDOWN
    if "json_to_excel" in modes:
        return DOWNLOAD_MODE_JSON
    return report_cfg.get("download_mode") or DOWNLOAD_MODE_FILE


def download_to_form_row(item: dict) -> dict:
    body_type = default_body_type_for_download(item)
    data_value = item.get("json") if "json" in item and "data" not in item else item.get("data", {})
    excel_cfg = item.get("excel") or {}
    drilldown_cfg = item.get("drilldown") or {}
    return {
        "name": item.get("name", ""),
        "stage": item.get("stage", "report_analysis"),
        "auth_preset": infer_auth_preset(item),
        "method": item.get("method", "POST"),
        "url": item.get("url", ""),
        "headers_json": json_dumps_for_cell(default_headers_for_download(item)),
        "headers_from_cookies_json": json_dumps_for_cell(default_headers_from_cookies_for_download(item)),
        "csrf_headers_from_cookies_json": json_dumps_for_cell(default_csrf_headers_from_cookies_for_download(item)),
        "headers_from_session_storage_json": json_dumps_for_cell(
            normalize_storage_mapping(item.get("headers_from_session_storage") or {})
        ),
        "headers_from_local_storage_json": json_dumps_for_cell(
            normalize_storage_mapping(item.get("headers_from_local_storage") or {})
        ),
        "body_type": body_type,
        "data_json": json_dumps_for_cell(data_value or {}),
        "raw_body": item.get("raw_body", ""),
        "response_mode": item.get("response_mode", "file"),
        "headers_from_cookie_string_json": json_dumps_for_cell(item.get("headers_from_cookie_string") or {}),
        "excel_data_path": excel_cfg.get("data_path", "result.tableData"),
        "excel_sheet_name": excel_cfg.get("sheet_name", "地市作战明细"),
        "excel_columns_json": json_dumps_for_cell(excel_cfg.get("columns") or []),
        "drilldown_levels": ",".join(drilldown_cfg.get("levels") or ["区县", "网格", "渠道/门店", "人员"]),
        "drilldown_next_area_field": drilldown_cfg.get("next_area_field", "areaCode"),
        "drilldown_request_area_field": drilldown_cfg.get("request_area_field", "areaId"),
        "drilldown_max_requests": drilldown_cfg.get("max_requests", 1000),
    }


def normalize_table_rows(rows) -> list[dict]:
    if rows is None:
        return []
    if hasattr(rows, "to_dict"):
        return rows.to_dict("records")
    return [dict(row) for row in rows]


def is_blank_value(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def parse_optional_int(value, default=None):
    if is_blank_value(value):
        return default
    return int(float(value))


def downloads_to_form_rows(downloads: list[dict]) -> list[dict]:
    rows = [download_to_form_row(item or {}) for item in downloads]
    return rows or [download_to_form_row({})]


def apply_default_stage_to_rows(rows: list[dict], default_stage: str) -> list[dict]:
    normalized = normalize_table_rows(rows)
    for row in normalized:
        if not (row.get("stage") or "").strip():
            row["stage"] = default_stage
    return normalized


def non_empty_download_rows(rows: list[dict]) -> list[dict]:
    kept = []
    for row in normalize_table_rows(rows):
        if any(
            str(row.get(key) or "").strip()
            for key in ("name", "url", "headers_json", "data_json", "raw_body")
        ):
            kept.append(row)
    return kept


def non_empty_compare_rows(rows: list[dict]) -> list[dict]:
    kept = []
    for row in normalize_table_rows(rows):
        if any(
            str(row.get(key) or "").strip()
            for key in (
                "download_name",
                "name",
                "new_sheet_name",
                "template_sheet_name",
                "header_row",
                "ignore_columns",
                "key_columns",
            )
        ):
            kept.append(row)
    return kept


def empty_compare_row(download_name: str = "") -> dict:
    return {
        "download_name": download_name,
        "name": "",
        "new_sheet_name": "",
        "template_sheet_name": "",
        "header_row": None,
        "ignore_columns": "",
        "key_columns": "",
    }


def non_empty_send_rows(rows: list[dict]) -> list[dict]:
    kept = []
    for row in normalize_table_rows(rows):
        if any(str(row.get(key) or "").strip() for key in ("type", "sheet", "text_mode", "extra_json")):
            kept.append(row)
    return kept


def empty_send_row() -> dict:
    return {
        "type": "image",
        "sheet": "",
        "text_mode": "",
        "extra_json": "{}",
    }


def form_rows_to_downloads(rows: list[dict]) -> tuple[list[dict], list[str]]:
    rows = normalize_table_rows(rows)
    downloads = []
    errors = []
    for index, row in enumerate(rows, start=1):
        name = (row.get("name") or "").strip()
        stage = (row.get("stage") or "").strip()
        auth_preset = row.get("auth_preset") or AUTH_PRESET_NONE
        method = (row.get("method") or "POST").strip().upper()
        url = (row.get("url") or "").strip()
        headers_raw = row.get("headers_json") or "{}"
        headers_from_cookies_raw = row.get("headers_from_cookies_json") or "{}"
        csrf_headers_from_cookies_raw = row.get("csrf_headers_from_cookies_json") or "{}"
        headers_from_session_storage_raw = row.get("headers_from_session_storage_json") or "{}"
        headers_from_local_storage_raw = row.get("headers_from_local_storage_json") or "{}"
        headers_from_cookie_string_raw = row.get("headers_from_cookie_string_json") or "{}"
        body_type = (row.get("body_type") or "form").strip().lower()
        data_raw = row.get("data_json") or "{}"
        raw_body = row.get("raw_body") or ""
        response_mode = (row.get("response_mode") or "file").strip().lower()
        if not any([
            name,
            stage,
            url,
            data_raw.strip() not in {"", "{}"},
            raw_body.strip(),
        ]):
            continue
        if body_type not in {"json", "form", "raw"}:
            errors.append(f"downloads 第 {index} 行 body_type 只支持 json/form/raw")
            body_type = "form"
        try:
            data = json.loads(data_raw or "{}")
            if body_type == "form" and not isinstance(data, dict):
                errors.append(f"downloads 第 {index} 行 form 类型 data_json 必须是对象 JSON")
                data = {}
        except json.JSONDecodeError as exc:
            errors.append(f"downloads 第 {index} 行 data_json 格式错误: {exc}")
            data = {}
        try:
            headers = json.loads(headers_raw or "{}")
            if not isinstance(headers, dict):
                errors.append(f"downloads 第 {index} 行 headers_json 必须是对象 JSON")
                headers = {}
        except json.JSONDecodeError as exc:
            errors.append(f"downloads 第 {index} 行 headers_json 格式错误: {exc}")
            headers = {}
        try:
            headers_from_cookies = json.loads(headers_from_cookies_raw or "{}")
            if not isinstance(headers_from_cookies, dict):
                errors.append(f"downloads 第 {index} 行 headers_from_cookies_json 必须是对象 JSON")
                headers_from_cookies = {}
        except json.JSONDecodeError as exc:
            errors.append(f"downloads 第 {index} 行 headers_from_cookies_json 格式错误: {exc}")
            headers_from_cookies = {}
        try:
            csrf_headers_from_cookies = json.loads(csrf_headers_from_cookies_raw or "{}")
            if not isinstance(csrf_headers_from_cookies, dict):
                errors.append(f"downloads 第 {index} 行 csrf_headers_from_cookies_json 必须是对象 JSON")
                csrf_headers_from_cookies = {}
        except json.JSONDecodeError as exc:
            errors.append(f"downloads 第 {index} 行 csrf_headers_from_cookies_json 格式错误: {exc}")
            csrf_headers_from_cookies = {}
        try:
            headers_from_session_storage = json.loads(headers_from_session_storage_raw or "{}")
            if not isinstance(headers_from_session_storage, dict):
                errors.append(f"downloads 第 {index} 行 headers_from_session_storage_json 必须是对象 JSON")
                headers_from_session_storage = {}
        except json.JSONDecodeError as exc:
            errors.append(f"downloads 第 {index} 行 headers_from_session_storage_json 格式错误: {exc}")
            headers_from_session_storage = {}
        try:
            headers_from_local_storage = json.loads(headers_from_local_storage_raw or "{}")
            if not isinstance(headers_from_local_storage, dict):
                errors.append(f"downloads 第 {index} 行 headers_from_local_storage_json 必须是对象 JSON")
                headers_from_local_storage = {}
        except json.JSONDecodeError as exc:
            errors.append(f"downloads 第 {index} 行 headers_from_local_storage_json 格式错误: {exc}")
            headers_from_local_storage = {}
        try:
            headers_from_cookie_string = json.loads(headers_from_cookie_string_raw or "{}")
            if not isinstance(headers_from_cookie_string, dict):
                errors.append(f"downloads 第 {index} 行 headers_from_cookie_string_json 必须是对象 JSON")
                headers_from_cookie_string = {}
        except json.JSONDecodeError as exc:
            errors.append(f"downloads 第 {index} 行 headers_from_cookie_string_json 格式错误: {exc}")
            headers_from_cookie_string = {}
        if response_mode not in {"file", "json_to_excel", "json_drilldown_to_excel"}:
            errors.append(f"downloads 第 {index} 行 response_mode 只支持 file/json_to_excel/json_drilldown_to_excel")
            response_mode = "file"
        if not name:
            errors.append(f"downloads 第 {index} 行缺少 name")
        if not stage:
            errors.append(f"downloads 第 {index} 行缺少 stage")
        if not url:
            errors.append(f"downloads 第 {index} 行缺少 url")
        item = {
            "name": name,
            "stage": stage,
            "auth_preset": auth_preset,
            "method": method,
            "url": url,
            "headers": headers,
            "body_type": body_type,
            "response_mode": response_mode,
        }
        if headers_from_cookies:
            item["headers_from_cookies"] = headers_from_cookies
        if csrf_headers_from_cookies:
            item["csrf_headers_from_cookies"] = csrf_headers_from_cookies
        if headers_from_session_storage:
            item["headers_from_session_storage"] = normalize_storage_mapping(headers_from_session_storage)
        if headers_from_local_storage:
            item["headers_from_local_storage"] = normalize_storage_mapping(headers_from_local_storage)
        if headers_from_cookie_string:
            item["headers_from_cookie_string"] = headers_from_cookie_string
        item["headers"] = remove_dynamic_headers_from_static(
            item.get("headers") or {},
            item.get("headers_from_cookies") or {},
            item.get("headers_from_session_storage") or {},
            item.get("headers_from_local_storage") or {},
        )
        if auth_preset != AUTH_PRESET_CUSTOM:
            item = apply_auth_preset_to_item(item, auth_preset)
            item["headers"] = remove_dynamic_headers_from_static(
                item.get("headers") or {},
                item.get("headers_from_cookies") or {},
                item.get("headers_from_session_storage") or {},
                item.get("headers_from_local_storage") or {},
            )
        if body_type == "raw":
            item["raw_body"] = raw_body
        else:
            item["data"] = data
        if response_mode in {"json_to_excel", "json_drilldown_to_excel"}:
            try:
                excel_columns = json.loads(row.get("excel_columns_json") or "[]")
                if not isinstance(excel_columns, list):
                    errors.append(f"downloads 第 {index} 行 excel_columns_json 必须是数组 JSON")
                    excel_columns = []
            except json.JSONDecodeError as exc:
                errors.append(f"downloads 第 {index} 行 excel_columns_json 格式错误: {exc}")
                excel_columns = []
            item["excel"] = {
                "data_path": (row.get("excel_data_path") or "result.tableData").strip(),
                "sheet_name": (row.get("excel_sheet_name") or "地市作战明细").strip(),
                "columns": excel_columns,
            }
        if response_mode == "json_drilldown_to_excel":
            item["drilldown"] = {
                "data_path": (row.get("excel_data_path") or "result.tableData").strip(),
                "request_area_field": (row.get("drilldown_request_area_field") or "areaId").strip(),
                "next_area_field": (row.get("drilldown_next_area_field") or "areaCode").strip(),
                "levels": [part.strip() for part in (row.get("drilldown_levels") or "").split(",") if part.strip()],
                "max_requests": parse_optional_int(row.get("drilldown_max_requests"), 1000),
            }
        downloads.append(item)
    return downloads, errors


def form_rows_to_download_drafts(rows: list[dict], default_stage: str) -> list[dict]:
    downloads = []
    for row in apply_default_stage_to_rows(rows, default_stage):
        item = {
            "name": (row.get("name") or "").strip(),
            "stage": (row.get("stage") or default_stage or "").strip(),
            "auth_preset": row.get("auth_preset") or AUTH_PRESET_NONE,
            "method": (row.get("method") or "POST").strip().upper(),
            "url": (row.get("url") or "").strip(),
            "body_type": (row.get("body_type") or "form").strip().lower(),
        }
        for source_key, target_key, default_value in (
            ("headers_json", "headers", {}),
            ("headers_from_cookies_json", "headers_from_cookies", {}),
            ("csrf_headers_from_cookies_json", "csrf_headers_from_cookies", {}),
            ("headers_from_session_storage_json", "headers_from_session_storage", {}),
            ("headers_from_local_storage_json", "headers_from_local_storage", {}),
            ("headers_from_cookie_string_json", "headers_from_cookie_string", {}),
            ("data_json", "data", {}),
        ):
            try:
                parsed = json.loads(row.get(source_key) or "{}")
            except json.JSONDecodeError:
                parsed = default_value
            item[target_key] = parsed
        item["headers_from_session_storage"] = normalize_storage_mapping(
            item.get("headers_from_session_storage") or {}
        )
        item["headers_from_local_storage"] = normalize_storage_mapping(
            item.get("headers_from_local_storage") or {}
        )
        if row.get("raw_body"):
            item["raw_body"] = row.get("raw_body")
        response_mode = (row.get("response_mode") or "file").strip().lower()
        item["response_mode"] = response_mode
        if response_mode in {"json_to_excel", "json_drilldown_to_excel"}:
            try:
                columns = json.loads(row.get("excel_columns_json") or "[]")
            except json.JSONDecodeError:
                columns = []
            item["excel"] = {
                "data_path": (row.get("excel_data_path") or "result.tableData").strip(),
                "sheet_name": (row.get("excel_sheet_name") or "地市作战明细").strip(),
                "columns": columns,
            }
        if response_mode == "json_drilldown_to_excel":
            item["drilldown"] = {
                "data_path": (row.get("excel_data_path") or "result.tableData").strip(),
                "request_area_field": (row.get("drilldown_request_area_field") or "areaId").strip(),
                "next_area_field": (row.get("drilldown_next_area_field") or "areaCode").strip(),
                "levels": [part.strip() for part in (row.get("drilldown_levels") or "").split(",") if part.strip()],
                "max_requests": parse_optional_int(row.get("drilldown_max_requests"), 1000),
            }
        if item.get("auth_preset") != AUTH_PRESET_CUSTOM:
            item = apply_auth_preset_to_item(item, item.get("auth_preset"))
        item["headers"] = remove_dynamic_headers_from_static(
            item.get("headers") or {},
            item.get("headers_from_cookies") or {},
            item.get("headers_from_session_storage") or {},
            item.get("headers_from_local_storage") or {},
        )
        if any(value not in ("", {}, []) for value in item.values()):
            downloads.append(item)
    return downloads


def compare_sources_to_form_rows(compare_sources: list[dict]) -> list[dict]:
    rows = []
    for source in compare_sources or []:
        download_name = source.get("download_name", "")
        for mapping in source.get("sheet_mappings") or []:
            rows.append({
                "download_name": download_name,
                "name": mapping.get("name", ""),
                "new_sheet_name": mapping.get("new_sheet_name", ""),
                "template_sheet_name": mapping.get("template_sheet_name", ""),
                "header_row": parse_optional_int(mapping.get("header_row")),
                "ignore_columns": ",".join(mapping.get("ignore_columns") or []),
                "key_columns": ",".join(mapping.get("key_columns") or []),
            })
    return rows or [empty_compare_row()]


def sheet_mappings_to_form_rows(sheet_mappings: list[dict], download_name: str = "") -> list[dict]:
    rows = []
    for mapping in sheet_mappings or []:
        if isinstance(mapping, str):
            mapping = {"name": mapping, "new_sheet_name": mapping, "template_sheet_name": mapping}
        rows.append({
            "download_name": download_name,
            "name": mapping.get("name", ""),
            "new_sheet_name": mapping.get("new_sheet_name") or mapping.get("new_sheet") or mapping.get("sheet") or "",
            "template_sheet_name": mapping.get("template_sheet_name") or mapping.get("template_sheet") or mapping.get("new_sheet_name") or "",
            "header_row": parse_optional_int(mapping.get("header_row")),
            "ignore_columns": ",".join(mapping.get("ignore_columns") or []),
            "key_columns": ",".join(mapping.get("key_columns") or []),
        })
    return rows or [empty_compare_row(download_name)]


def form_rows_to_sheet_mappings(rows: list[dict]) -> tuple[list[dict], list[str]]:
    rows = normalize_table_rows(rows)
    mappings = []
    errors = []
    for index, row in enumerate(rows, start=1):
        new_sheet_name = (row.get("new_sheet_name") or "").strip()
        template_sheet_name = (row.get("template_sheet_name") or "").strip()
        if not any([new_sheet_name, template_sheet_name]):
            continue
        if not new_sheet_name:
            errors.append(f"sheet_mappings 第 {index} 行缺少 new_sheet_name")
        if not template_sheet_name:
            errors.append(f"sheet_mappings 第 {index} 行缺少 template_sheet_name")
        mappings.append({
            "name": (row.get("name") or new_sheet_name or template_sheet_name).strip(),
            "new_sheet_name": new_sheet_name,
            "template_sheet_name": template_sheet_name,
            "header_row": parse_optional_int(row.get("header_row"), default=1),
            "ignore_columns": [c.strip() for c in (row.get("ignore_columns") or "").split(",") if c.strip()],
            "key_columns": [c.strip() for c in (row.get("key_columns") or "").split(",") if c.strip()],
        })
    return mappings, errors


def form_rows_to_compare_sources(rows: list[dict]) -> tuple[list[dict], list[str]]:
    rows = normalize_table_rows(rows)
    grouped: dict[str, list[dict]] = {}
    errors = []
    for index, row in enumerate(rows, start=1):
        download_name = (row.get("download_name") or "").strip()
        new_sheet_name = (row.get("new_sheet_name") or "").strip()
        template_sheet_name = (row.get("template_sheet_name") or "").strip()
        if not any([download_name, new_sheet_name, template_sheet_name]):
            continue
        if not download_name:
            errors.append(f"compare_sources 第 {index} 行缺少 download_name")
        if not new_sheet_name:
            errors.append(f"compare_sources 第 {index} 行缺少 new_sheet_name")
        if not template_sheet_name:
            errors.append(f"compare_sources 第 {index} 行缺少 template_sheet_name")
        mapping = {
            "name": (row.get("name") or new_sheet_name or template_sheet_name).strip(),
            "new_sheet_name": new_sheet_name,
            "template_sheet_name": template_sheet_name,
            "header_row": parse_optional_int(row.get("header_row"), default=1),
            "ignore_columns": [c.strip() for c in (row.get("ignore_columns") or "").split(",") if c.strip()],
            "key_columns": [c.strip() for c in (row.get("key_columns") or "").split(",") if c.strip()],
        }
        grouped.setdefault(download_name, []).append(mapping)
    return [
        {"download_name": download_name, "sheet_mappings": mappings}
        for download_name, mappings in grouped.items()
    ], errors


def form_rows_to_compare_source_drafts(rows: list[dict]) -> list[dict]:
    rows = normalize_table_rows(rows)
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        download_name = (row.get("download_name") or "").strip()
        mapping = {
            "name": (row.get("name") or "").strip(),
            "new_sheet_name": (row.get("new_sheet_name") or "").strip(),
            "template_sheet_name": (row.get("template_sheet_name") or "").strip(),
            "header_row": parse_optional_int(row.get("header_row")),
            "ignore_columns": [c.strip() for c in (row.get("ignore_columns") or "").split(",") if c.strip()],
            "key_columns": [c.strip() for c in (row.get("key_columns") or "").split(",") if c.strip()],
        }
        if not download_name and not any(value not in ("", [], None) for value in mapping.values()):
            continue
        grouped.setdefault(download_name, []).append(mapping)
    return [
        {"download_name": download_name, "sheet_mappings": mappings}
        for download_name, mappings in grouped.items()
    ]


def send_items_to_form_rows(items: list[dict]) -> list[dict]:
    rows = []
    for item in items or []:
        text_cfg = item.get("text") or {}
        extra = {
            key: value
            for key, value in item.items()
            if key not in {"type", "sheet", "text"}
        }
        if item.get("type") == "text":
            extra_text = {key: value for key, value in text_cfg.items() if key != "mode"}
            if extra_text:
                extra["text"] = extra_text
        rows.append({
            "type": item.get("type", "image"),
            "sheet": item.get("sheet", ""),
            "text_mode": text_cfg.get("mode", "used_range") if item.get("type") == "text" else "",
            "extra_json": json.dumps(extra, ensure_ascii=False, indent=2) if extra else "{}",
        })
    return rows or [empty_send_row()]


def form_rows_to_send_items(rows: list[dict]) -> tuple[list[dict], list[str]]:
    rows = normalize_table_rows(rows)
    items = []
    errors = []
    for index, row in enumerate(rows, start=1):
        item_type = (row.get("type") or "").strip()
        sheet = (row.get("sheet") or "").strip()
        if not any([item_type, sheet]):
            continue
        if item_type not in {"image", "text"}:
            errors.append(f"items 第 {index} 行 type 只支持 image/text")
        if not sheet:
            errors.append(f"items 第 {index} 行缺少 sheet")
        extra_raw = row.get("extra_json") or "{}"
        try:
            extra = json.loads(extra_raw)
            if not isinstance(extra, dict):
                errors.append(f"items 第 {index} 行 extra_json 必须是对象 JSON")
                extra = {}
        except json.JSONDecodeError as exc:
            errors.append(f"items 第 {index} 行 extra_json 格式错误: {exc}")
            extra = {}
        item = {**extra, "type": item_type or "image", "sheet": sheet}
        if item["type"] == "text":
            text_cfg = item.get("text") if isinstance(item.get("text"), dict) else {}
            text_mode = (row.get("text_mode") or "used_range").strip()
            item["text"] = {**text_cfg, "mode": text_mode}
        else:
            item.pop("text", None)
        items.append(item)
    return items, errors


def form_rows_to_send_item_drafts(rows: list[dict]) -> list[dict]:
    items = []
    for row in normalize_table_rows(rows):
        item_type = (row.get("type") or "image").strip()
        sheet = (row.get("sheet") or "").strip()
        try:
            extra = json.loads(row.get("extra_json") or "{}")
            if not isinstance(extra, dict):
                extra = {}
        except json.JSONDecodeError:
            extra = {}
        if not any([item_type, sheet, extra]):
            continue
        item = {**extra, "type": item_type, "sheet": sheet}
        if item_type == "text":
            text_cfg = item.get("text") if isinstance(item.get("text"), dict) else {}
            item["text"] = {**text_cfg, "mode": (row.get("text_mode") or "used_range").strip()}
        items.append(item)
    return items


def build_report_config_payload(
    name,
    template_path,
    primary_download,
    downloads,
    compare_sheet_mappings,
    compare_sources,
    send_webhook_url,
    send_workbook_name,
    send_items,
    template_update,
    use_multi_report,
    download_mode,
    draft=False,
) -> dict:
    report_cfg = {
        "name": name,
        "template_path": template_path,
        "download": primary_download,
        "compare": {
            "sheet_mappings": compare_sheet_mappings,
            "header_row": 1,
            "ignore_columns": [],
            "key_columns": [],
        },
        "send": {
            "webhook_url": send_webhook_url.strip(),
            "workbook_name": send_workbook_name,
            "items": send_items,
        },
        "template_update": template_update,
        "download_mode": download_mode,
    }
    if use_multi_report:
        report_cfg["downloads"] = downloads
        report_cfg["compare_sources"] = compare_sources
    if draft:
        report_cfg["draft"] = True
    return report_cfg


def resolve_placeholder_preview(payload):
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    day_before_yesterday = now - timedelta(days=2)
    replacements = {
        "${today}": now.strftime("%Y-%m-%d"),
        "${today_yyyymmdd}": now.strftime("%Y%m%d"),
        "${yesterday}": yesterday.strftime("%Y-%m-%d"),
        "${yesterday_yyyymmdd}": yesterday.strftime("%Y%m%d"),
        "${day_before_yesterday}": day_before_yesterday.strftime("%Y-%m-%d"),
        "${day_before_yesterday_yyyymmdd}": day_before_yesterday.strftime("%Y%m%d"),
        "${hour}": str(now.hour),
        "${hour2}": now.strftime("%H"),
    }

    def _resolve(value):
        if isinstance(value, dict):
            return {k: _resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_resolve(v) for v in value]
        if isinstance(value, str):
            resolved = value
            for token, token_value in replacements.items():
                resolved = resolved.replace(token, token_value)
            return resolved
        return value

    return _resolve(payload), replacements


# ── UI ──────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="通报管理", layout="wide")
st.title("通报配置管理")

reports = list_reports()
report_names = [report_name_from_path(p) for p in reports]

col_list, col_edit = st.columns([0.6, 2.4])

with col_list:
    st.subheader("报表列表")
    selected = st.radio("选择报表", ["+ 新建报表"] + report_names, label_visibility="collapsed")

with col_edit:
    is_new = selected == "+ 新建报表"
    st.subheader("新建报表" if is_new else f"编辑：{selected}")

    if is_new:
        default = {
            "name": "",
            "template_path": "templates/",
            "download": {
                "name": "",
                "stage": "report_analysis",
                "url": "",
                "data": {}
            },
            "compare": {
                "sheet_mappings": [],
                "header_row": 1,
                "ignore_columns": [],
                "key_columns": []
            },
            "send": {
                "workbook_name": "",
                "items": []
            },
            "template_update": {
                "update_condition": "any_changed",
                "write_sheets": "changed",
                "send_when_same": False
            }
        }
    else:
        report_path = REPORTS_DIR / f"{selected}.json"
        default = load_json(report_path)

    if is_new:
        task_default = {
            "wait_for_change": {
                "enabled": True,
                "poll_interval_seconds": 300,
                "max_wait_minutes": 180,
            }
        }
    else:
        task_path_existing = task_config_path(selected)
        if task_path_existing.exists():
            task_default = load_json(task_path_existing)
        else:
            task_default = {
                "wait_for_change": {
                    "enabled": True,
                    "poll_interval_seconds": 300,
                    "max_wait_minutes": 180,
                }
            }

    # 基本信息
    name = st.text_input(
        "通报名称",
        value=default.get("name", ""),
        help="用于保存 config/reports 下的配置文件名，以及 Prefect 部署名称。",
    )
    current_template_path = default.get("template_path", "templates/")
    template_options = list_template_files()
    template_choice_options = ["手动输入"] + template_options
    default_template_choice = current_template_path if current_template_path in template_options else "手动输入"
    template_choice = st.selectbox(
        "模板文件选择",
        options=template_choice_options,
        index=template_choice_options.index(default_template_choice),
        help="优先从 templates 目录选择 Excel 模板；如果模板在其他目录，可以选择“手动输入”。",
    )
    if template_choice == "手动输入":
        template_path = st.text_input("模板路径", value=current_template_path)
    else:
        template_path = template_choice
        st.text_input("模板路径", value=template_path, disabled=True)

    # 下载参数
    st.markdown("**下载报表**")
    dl = default.get("download", {})
    current_download_mode = infer_download_mode(default)
    download_mode = st.radio(
        "下载模式",
        DOWNLOAD_MODE_OPTIONS,
        index=DOWNLOAD_MODE_OPTIONS.index(current_download_mode)
        if current_download_mode in DOWNLOAD_MODE_OPTIONS else 0,
        horizontal=True,
        help="原始文件下载适合接口直接返回 Excel；JSON 转 Excel适合接口返回 JSON；级联下钻适合地市作战逐级请求。",
    )
    stage_options = list_stage_options()
    current_stage = dl.get("stage", "report_analysis")
    if current_stage not in stage_options:
        stage_options = [current_stage] + stage_options
    dl_stage = st.selectbox(
        "默认 Cookie Stage",
        options=stage_options,
        index=stage_options.index(current_stage),
        help=(
            "用于给新增下载行提供默认 Cookie。"
            "该值需与 login_config.json 中 usm_cookie_apps 的 stage 一致。"
            "若选错，下载接口可能返回登录页或报“找不到 stage”。"
        ),
    )
    stage_title_map = load_stage_title_map_from_dump()
    stage_explain_lines = []
    for stage in stage_options:
        title = stage_title_map.get(stage) or "（未在 runtime/cookies/cookie_dump.json 中找到 title）"
        stage_explain_lines.append(f"- `{stage}`：{title}")
    st.markdown("Cookie Stage 对应说明（来自 cookie_dump.title）：\n" + "\n".join(stage_explain_lines))

    selected_marker = selected if not is_new else "__new__"
    has_multi_config = bool(default.get("downloads") or default.get("compare_sources"))
    initial_download_rows = default.get("downloads") or [default.get("download", {})]
    primary_download_name = (initial_download_rows[0] or {}).get("name") or default.get("name", "")
    init_state_value(
        "downloads_rows_marker",
        "downloads_rows",
        selected_marker,
        downloads_to_form_rows(initial_download_rows),
    )
    init_state_value(
        "compare_rows_marker",
        "compare_rows",
        selected_marker,
        compare_sources_to_form_rows(default.get("compare_sources") or [])
        if default.get("compare_sources")
        else sheet_mappings_to_form_rows(default.get("compare", {}).get("sheet_mappings", []), primary_download_name),
    )
    init_state_value(
        "send_rows_marker",
        "send_rows",
        selected_marker,
        send_items_to_form_rows(default.get("send", {}).get("items", [])),
    )

    st.caption("一行就是一个下载请求；下载标识用于后续比对匹配，也会作为落盘文件名前缀，避免同名 Excel 覆盖。")
    st.caption(
        "占位符支持：`${today}`(YYYY-MM-DD)、`${yesterday}`(前一天 YYYY-MM-DD)、"
        "`${day_before_yesterday}`(前天 YYYY-MM-DD)、`${today_yyyymmdd}`(今天 YYYYMMDD)、"
        "`${yesterday_yyyymmdd}`(前一天 YYYYMMDD)、`${day_before_yesterday_yyyymmdd}`(前天 YYYYMMDD)、"
        "`${hour}`(0-23)、`${hour2}`(00-23)、"
        "`${session_storage:zhyyptInfo.accessToken}`、`${local_storage:tokenInfo.accessToken}`。"
    )
    st.session_state["downloads_rows"] = apply_default_stage_to_rows(st.session_state["downloads_rows"], dl_stage)
    mode_response_value = {
        DOWNLOAD_MODE_FILE: "file",
        DOWNLOAD_MODE_JSON: "json_to_excel",
        DOWNLOAD_MODE_DRILLDOWN: "json_drilldown_to_excel",
    }[download_mode]
    for row in st.session_state["downloads_rows"]:
        row["response_mode"] = mode_response_value
        if download_mode in {DOWNLOAD_MODE_JSON, DOWNLOAD_MODE_DRILLDOWN}:
            row.setdefault("stage", "city_ops")
            row["body_type"] = "json"
            row.setdefault("excel_data_path", "result.tableData")
            row.setdefault("excel_sheet_name", "地市作战明细")
            row.setdefault("excel_columns_json", json_dumps_for_cell([
                {"field": "__level_name", "header": "层级"},
                {"field": "__parent_area_id", "header": "父级areaId"},
                {"field": "__request_area_id", "header": "请求areaId"},
                {"field": "areaName", "header": "名称"},
                {"field": "areaCode", "header": "编码"},
                {"field": "sgs_ajvwdz", "header": "爱家亲情网(V网版)"},
            ]))
            row.setdefault("headers_from_cookie_string_json", json_dumps_for_cell({"uapToken": "*"}))
            if download_mode == DOWNLOAD_MODE_DRILLDOWN:
                row.setdefault("drilldown_levels", "区县,网格,渠道/门店,人员")
                row.setdefault("drilldown_next_area_field", "areaCode")
                row.setdefault("drilldown_request_area_field", "areaId")
                row.setdefault("drilldown_max_requests", 1000)
    dl_btn_col1, dl_btn_col2 = st.columns([1, 5])
    if dl_btn_col1.button("新增下载行"):
        rows = non_empty_download_rows(st.session_state["downloads_rows"])
        rows.append(download_to_form_row({"stage": dl_stage}))
        st.session_state["downloads_rows"] = rows
        st.rerun()
    if dl_btn_col2.button("清理空白行"):
        rows = non_empty_download_rows(st.session_state["downloads_rows"])
        st.session_state["downloads_rows"] = rows or [download_to_form_row({"stage": dl_stage})]
        st.rerun()
    if download_mode == DOWNLOAD_MODE_FILE:
        download_column_order = [
            "name",
            "stage",
            "auth_preset",
            "method",
            "url",
            "headers_json",
            "body_type",
            "data_json",
            "raw_body",
        ]
    elif download_mode == DOWNLOAD_MODE_JSON:
        download_column_order = [
            "name",
            "stage",
            "method",
            "url",
            "headers_json",
            "headers_from_cookie_string_json",
            "data_json",
            "excel_data_path",
            "excel_sheet_name",
            "excel_columns_json",
        ]
    else:
        download_column_order = [
            "name",
            "stage",
            "method",
            "url",
            "headers_json",
            "headers_from_cookie_string_json",
            "data_json",
            "excel_data_path",
            "drilldown_levels",
            "drilldown_max_requests",
            "excel_sheet_name",
            "excel_columns_json",
        ]
    downloads_rows = st.data_editor(
        st.session_state["downloads_rows"],
        key="downloads_rows_editor",
        num_rows="fixed",
        width="stretch",
        column_order=download_column_order,
        column_config={
            "name": st.column_config.TextColumn("下载标识", required=True),
            "stage": st.column_config.SelectboxColumn("Cookie Stage", options=stage_options, required=True),
            "auth_preset": st.column_config.SelectboxColumn(
                "动态认证",
                options=AUTH_PRESET_OPTIONS,
                required=True,
                help=(
                    "无：不自动替换认证；SSR Cookie：从 Cookie 取 ssr-token；"
                    "智慧运营 User-Info：从 sessionStorage.zhyyptInfo.accessToken 取 User-Info；"
                    "自定义高级：使用下方高级认证配置。"
                ),
            ),
            "method": st.column_config.SelectboxColumn("请求方法", options=["POST", "GET", "PUT", "PATCH", "DELETE"], required=True),
            "url": st.column_config.TextColumn("下载 URL", required=True),
            "headers_json": st.column_config.TextColumn(
                "固定请求头 JSON",
                width="medium",
                help="只显示不会过期的请求头，例如 Content-Type、Accept；User-Info/ssr-token 建议用动态认证。",
            ),
            "body_type": st.column_config.SelectboxColumn("载体类型", options=["form", "json", "raw"], required=True),
            "data_json": st.column_config.TextColumn("请求体 JSON/form", width="large"),
            "raw_body": st.column_config.TextColumn("原始请求体 raw", width="medium"),
            "response_mode": st.column_config.TextColumn("响应模式"),
            "headers_from_cookie_string_json": st.column_config.TextColumn(
                "Cookie串转Header JSON",
                width="medium",
                help='地市作战常用：{"uapToken":"*"}，表示把当前 Cookie Stage 的 Cookie 串放入 uapToken 请求头。',
            ),
            "excel_data_path": st.column_config.TextColumn("JSON数据路径", help="地市作战一般填写 result.tableData"),
            "excel_sheet_name": st.column_config.TextColumn("Excel Sheet"),
            "excel_columns_json": st.column_config.TextColumn("Excel列配置 JSON", width="large"),
            "drilldown_levels": st.column_config.TextColumn("下钻层级", help="逗号分隔，例如：区县,网格,渠道/门店,人员"),
            "drilldown_next_area_field": st.column_config.TextColumn("下一层字段"),
            "drilldown_request_area_field": st.column_config.TextColumn("请求area字段"),
            "drilldown_max_requests": st.column_config.NumberColumn("最大请求数", min_value=1, step=1),
            "headers_from_cookies_json": st.column_config.TextColumn("Cookie 转 Header JSON", width="medium"),
            "csrf_headers_from_cookies_json": st.column_config.TextColumn("动态 CSRF JSON", width="medium"),
            "headers_from_session_storage_json": st.column_config.TextColumn(
                "会话存储转 Header JSON",
                width="medium",
                help='例如 {"User-Info": "zhyyptInfo.accessToken"}，从当前 Cookie Stage 的 sessionStorage 取值后放入请求头。',
            ),
            "headers_from_local_storage_json": st.column_config.TextColumn(
                "本地存储转 Header JSON",
                width="medium",
                help='例如 {"Authorization": "tokenInfo.accessToken"}，从当前 Cookie Stage 的 localStorage 取值后放入请求头。',
            ),
        },
    )
    download_rows_normalized = normalize_table_rows(downloads_rows)

    with st.expander("高级认证配置（一般不用改）", expanded=False):
        auth_target_options = [
            f"{index + 1}. {(row.get('name') or '未命名下载')}"
            for index, row in enumerate(download_rows_normalized)
        ] or ["1. 未命名下载"]
        auth_target_label = st.selectbox("配置哪一行下载", options=auth_target_options, key="download_auth_target")
        auth_target_index = auth_target_options.index(auth_target_label)
        auth_row = download_rows_normalized[auth_target_index] if download_rows_normalized else download_to_form_row({"stage": dl_stage})
        st.caption(
            "这里用于处理会变的认证字段。常见填写：SSR 用 Cookie 转 Header；智慧运营 User-Info 用会话存储转 Header。"
        )
        quick_col1, quick_col2 = st.columns(2)
        with quick_col1:
            if st.button("套用 SSR 默认认证", key="apply_ssr_auth_defaults"):
                rows = normalize_table_rows(downloads_rows)
                rows[auth_target_index]["auth_preset"] = AUTH_PRESET_SSR
                rows[auth_target_index]["headers_from_cookies_json"] = json_dumps_for_cell({"ssr-token": "ssr-token"})
                rows[auth_target_index]["csrf_headers_from_cookies_json"] = json_dumps_for_cell({"ssr-header": "ssr-token"})
                st.session_state["downloads_rows"] = rows
                st.rerun()
        with quick_col2:
            if st.button("套用智慧运营 User-Info", key="apply_smart_ops_auth_defaults"):
                rows = normalize_table_rows(downloads_rows)
                rows[auth_target_index]["auth_preset"] = AUTH_PRESET_SMART_OPS
                rows[auth_target_index]["headers_from_session_storage_json"] = json_dumps_for_cell(
                    {"User-Info": "zhyyptInfo.accessToken"}
                )
                st.session_state["downloads_rows"] = rows
                st.rerun()

        st.markdown("**固定请求头**")
        edited_headers_json = st.text_area(
            "固定请求头 JSON",
            value=auth_row.get("headers_json") or "{}",
            height=120,
            help="只放不会过期的请求头，例如 Content-Type、Accept。Cookie、user-info、ssr-token 这类变化字段建议在下面表格配置。",
        )

        st.markdown("**特殊请求头动态替换**")
        st.caption("请求头字段很多，这里只配置会变化的特殊字段；普通固定头保留在上面的请求头 JSON。")
        special_header_rows = st.data_editor(
            special_header_rows_from_download_row(auth_row),
            key=f"special_header_rows_{auth_target_index}",
            num_rows="dynamic",
            width="stretch",
            column_config={
                "enabled": st.column_config.CheckboxColumn("启用", default=True),
                "header_name": st.column_config.TextColumn("请求头字段", help="例如 User-Info、ssr-token、Authorization"),
                "source_type": st.column_config.SelectboxColumn(
                    "取值来源",
                    options=["Cookie", "sessionStorage", "localStorage"],
                    help="Cookie 适合 ssr-token；sessionStorage 适合 User-Info。",
                ),
                "source_path": st.column_config.TextColumn(
                    "来源字段/路径",
                    help="Cookie 填 Cookie 名；Storage 填路径，例如 zhyyptInfo.accessToken。",
                    width="large",
                ),
            },
        )

        edited_csrf_headers_json = st.text_area(
            "动态 CSRF JSON（SSR 专用，可空）",
            value=auth_row.get("csrf_headers_from_cookies_json") or "{}",
            height=80,
            help='例如 {"ssr-header": "ssr-token"}，从 Cookie ssr-header 取真正请求头名，从 ssr-token 取值。',
        )

        st.markdown("**请求体占位符配置**")
        body_placeholder_rows = st.data_editor(
            body_placeholder_rows_from_download_row(auth_row),
            key=f"body_placeholder_rows_{auth_target_index}",
            num_rows="dynamic",
            width="stretch",
            column_config={
                "enabled": st.column_config.CheckboxColumn("启用", default=False),
                "body_field": st.column_config.TextColumn(
                    "请求体字段",
                    help="要写入占位符的 body 字段，支持点路径，例如 ext.user_info。",
                ),
                "source_type": st.column_config.SelectboxColumn(
                    "占位符来源",
                    options=BODY_PLACEHOLDER_OPTIONS,
                    help="日期/小时不用填来源路径；Storage 需要填写来源字段/路径。",
                ),
                "source_path": st.column_config.TextColumn(
                    "来源字段/路径（Storage 时填写）",
                    help="选择 sessionStorage/localStorage 时填写，例如 zhyyptInfo.accessToken；选择日期/小时可留空。",
                    width="large",
                ),
            },
        )
        edited_data_json = st.text_area(
            "请求体 data/json",
            value=auth_row.get("data_json") or "{}",
            height=140,
            help="可以直接编辑请求体，也可以用上方表格给某个字段写入 Storage 占位符。",
        )
        if st.button("保存高级认证到当前下载行", key="save_download_auth_config"):
            rows = normalize_table_rows(downloads_rows)
            while len(rows) <= auth_target_index:
                rows.append(download_to_form_row({"stage": dl_stage}))
            cookie_headers = {}
            session_headers = {}
            local_headers = {}
            for special_row in normalize_table_rows(special_header_rows):
                if not special_row.get("enabled", True):
                    continue
                header_name = (special_row.get("header_name") or "").strip()
                source_type = special_row.get("source_type") or "sessionStorage"
                source_path = normalize_storage_path((special_row.get("source_path") or "").strip())
                if not header_name or not source_path:
                    continue
                if source_type == "Cookie":
                    cookie_headers[header_name] = source_path
                elif source_type == "sessionStorage":
                    session_headers[header_name] = source_path
                elif source_type == "localStorage":
                    local_headers[header_name] = source_path
            data_payload = parse_json_object(edited_data_json, {})
            for body_row in normalize_table_rows(body_placeholder_rows):
                if not body_row.get("enabled"):
                    continue
                body_field = (body_row.get("body_field") or "").strip()
                source_type = body_row.get("source_type") or "今天 YYYYMMDD"
                source_path = normalize_storage_path((body_row.get("source_path") or "").strip())
                if body_field and (source_type in BODY_PLACEHOLDER_MAP or source_path):
                    set_nested_dict_value(data_payload, body_field, storage_placeholder(source_type, source_path))
            static_headers = remove_dynamic_headers_from_static(
                parse_json_object(edited_headers_json, {}),
                cookie_headers,
                session_headers,
                local_headers,
            )
            rows[auth_target_index]["headers_json"] = json_dumps_for_cell(static_headers)
            rows[auth_target_index]["csrf_headers_from_cookies_json"] = edited_csrf_headers_json or "{}"
            rows[auth_target_index]["headers_from_cookies_json"] = json_dumps_for_cell(cookie_headers)
            rows[auth_target_index]["headers_from_session_storage_json"] = json_dumps_for_cell(session_headers)
            rows[auth_target_index]["headers_from_local_storage_json"] = json_dumps_for_cell(local_headers)
            rows[auth_target_index]["data_json"] = json_dumps_for_cell(data_payload)
            rows[auth_target_index]["auth_preset"] = AUTH_PRESET_CUSTOM
            st.session_state["downloads_rows"] = rows
            st.success("已保存高级认证配置")
            st.rerun()

    with st.expander("请求识别（粘贴 URL + 请求头 + 请求载体）", expanded=False):
        target_options = [
            f"{index + 1}. {(row.get('name') or '未命名下载')}"
            for index, row in enumerate(download_rows_normalized)
        ] or ["1. 未命名下载"]
        target_label = st.selectbox("回填到下载行", options=target_options)
        target_index = target_options.index(target_label)
        target_download_row = download_rows_normalized[target_index] if download_rows_normalized else {}
        st.caption(
            "推荐使用浏览器 Network 里的 Copy as cURL (bash)；也可以选择分开填写 URL、请求头和请求载体。"
        )
        parse_mode_options = {
            "推荐：cURL（bash）": "curl",
            "分开填写 URL + 请求头 + 请求载体": "headers_body",
            "请求头/完整 HTTP 请求": "raw_http",
        }
        with st.expander("实验性解析方式（不推荐，格式容易因浏览器版本变化）", expanded=False):
            experimental_mode_options = {
                "自动识别": "auto",
                "cURL（cmd）": "curl",
                "PowerShell": "powershell",
                "fetch": "fetch",
                "HAR": "har",
            }
            use_experimental_parser = st.checkbox("启用实验性解析方式")
            experimental_mode_label = st.selectbox(
                "实验性解析方式",
                options=list(experimental_mode_options.keys()),
                disabled=not use_experimental_parser,
                help="只有 cURL bash 目前作为推荐路径；其他格式建议在识别后仔细核对。",
            )
        parse_mode_label = st.selectbox(
            "解析方式",
            options=list(parse_mode_options.keys()),
            index=0,
            disabled=use_experimental_parser,
            help="优先使用 cURL（bash）；如果复制的是 Request URL / Request Headers / Payload，请选择分开填写。",
        )
        parse_mode = (
            experimental_mode_options[experimental_mode_label]
            if use_experimental_parser
            else parse_mode_options[parse_mode_label]
        )
        raw_request = st.text_area(
            "复制内容（可选）",
            value="",
            height=180,
            placeholder=(
                "推荐粘贴：Copy as cURL (bash)\n\n"
                "如果选择“分开填写”，这里可以留空，下面填写 URL、请求头、请求载体。"
            ),
        )
        show_split_fields = parse_mode == "headers_body"
        split_container = st.container() if show_split_fields else st.expander("高级：分开填写 / 补充字段", expanded=False)
        with split_container:
            req_col1, req_col2 = st.columns([1, 3])
            with req_col1:
                request_method = st.selectbox(
                    "请求方法",
                    options=["POST", "GET", "PUT", "PATCH", "DELETE"],
                    index=["POST", "GET", "PUT", "PATCH", "DELETE"].index((target_download_row.get("method") or "POST").upper())
                    if (target_download_row.get("method") or "POST").upper() in ["POST", "GET", "PUT", "PATCH", "DELETE"]
                    else 0,
                )
            with req_col2:
                request_url = st.text_input("请求 URL", value=target_download_row.get("url", ""))
            request_headers_text = st.text_area(
                "请求头原文",
                value="",
                height=120,
                placeholder="Accept: application/json, text/plain, */*\nContent-Type: application/json\nuser-info: ...",
            )
            request_body_text = st.text_area(
                "请求载体原文",
                value="",
                height=120,
                help="支持 JSON、a=1&b=2、按行 key=value / key: value；multipart/form-data 暂按 raw 保存。",
            )
        if st.button("识别请求", key="parse_download_request"):
            try:
                parsed_request = parse_request_by_mode(
                    parse_mode,
                    raw_request=raw_request,
                    method=request_method,
                    url=request_url,
                    headers_text=request_headers_text,
                    body=request_body_text,
                )
                st.session_state["download_request_parser_result"] = parsed_request
                st.session_state["download_request_parser_target_index"] = target_index
                st.success("识别成功，请确认要发送的请求头后回填")
                st.rerun()
            except Exception as exc:
                st.error(f"识别失败: {exc}")

        parsed_request = st.session_state.get("download_request_parser_result")
        parsed_target_index = st.session_state.get("download_request_parser_target_index")
        if parsed_request and parsed_target_index == target_index:
            st.markdown("**识别结果确认**")
            st.write(f"方法：`{parsed_request.get('method')}`，载体类型：`{parsed_request.get('body_type')}`")
            st.code(parsed_request.get("url", ""), language="text")
            st.caption("默认已取消 Cookie、Content-Length、Host、Connection、Accept-Encoding、sec-* 等浏览器运行时头。")
            parsed_header_names = {
                (row.get("name") or "").lower()
                for row in parsed_request.get("header_rows") or []
                if row.get("enabled", True)
            }
            recommendations = []
            if "user-info" in parsed_header_names or "userinfo" in parsed_header_names:
                recommendations.append("检测到 `User-Info`，回填时会默认改为从 `sessionStorage.zhyyptInfo.accessToken` 动态读取。")
            if "ssr-token" in parsed_header_names:
                recommendations.append("检测到 `ssr-token`，回填时会默认改为从 Cookie `ssr-token` 动态读取。")
            if recommendations:
                st.info("\n\n".join(recommendations))
            header_rows = st.data_editor(
                parsed_request.get("header_rows") or [],
                key="parsed_header_rows_editor",
                num_rows="dynamic",
                width="stretch",
                column_config={
                    "enabled": st.column_config.CheckboxColumn("发送", default=True),
                    "name": st.column_config.TextColumn("请求头字段", required=True),
                    "value": st.column_config.TextColumn("值", width="large"),
                },
            )
            if parsed_request.get("body_type") == "raw":
                st.text_area("raw body 预览", value=parsed_request.get("raw_body", ""), height=120, disabled=True)
            else:
                st.code(json.dumps(parsed_request.get("data", {}), ensure_ascii=False, indent=2), language="json")
            if st.button("确认回填到下载表格", key="apply_parsed_download_request"):
                rows = normalize_table_rows(downloads_rows)
                if not rows:
                    rows = downloads_to_form_rows([{}])
                while len(rows) <= target_index:
                    rows.append(download_to_form_row({}))
                selected_headers = headers_from_header_rows(normalize_table_rows(header_rows))
                cookie_header_mapping = parse_json_object(rows[target_index].get("headers_from_cookies_json"), {})
                session_header_mapping = parse_json_object(rows[target_index].get("headers_from_session_storage_json"), {})
                session_header_mapping = normalize_storage_mapping(session_header_mapping)
                for header_name in list(selected_headers.keys()):
                    if header_name.lower() in {"user-info", "userinfo"} and "User-Info" not in session_header_mapping:
                        selected_headers.pop(header_name, None)
                        session_header_mapping["User-Info"] = "zhyyptInfo.accessToken"
                    if header_name.lower() == "ssr-token" and "ssr-token" not in cookie_header_mapping:
                        selected_headers.pop(header_name, None)
                        cookie_header_mapping["ssr-token"] = "ssr-token"
                rows[target_index]["stage"] = rows[target_index].get("stage") or dl_stage
                if session_header_mapping.get("User-Info") == "zhyyptInfo.accessToken":
                    rows[target_index]["auth_preset"] = AUTH_PRESET_SMART_OPS
                elif cookie_header_mapping.get("ssr-token") == "ssr-token":
                    rows[target_index]["auth_preset"] = AUTH_PRESET_SSR
                else:
                    rows[target_index]["auth_preset"] = rows[target_index].get("auth_preset") or AUTH_PRESET_NONE
                rows[target_index]["method"] = parsed_request.get("method") or "POST"
                rows[target_index]["url"] = parsed_request.get("url") or rows[target_index].get("url", "")
                rows[target_index]["headers_json"] = json_dumps_for_cell(selected_headers)
                rows[target_index]["headers_from_cookies_json"] = json_dumps_for_cell(cookie_header_mapping)
                rows[target_index]["headers_from_session_storage_json"] = json_dumps_for_cell(session_header_mapping)
                rows[target_index]["body_type"] = parsed_request.get("body_type") or "form"
                rows[target_index]["data_json"] = json_dumps_for_cell(parsed_request.get("data", {}))
                rows[target_index]["raw_body"] = parsed_request.get("raw_body", "")
                if not rows[target_index].get("name"):
                    rows[target_index]["name"] = f"download-{target_index + 1}"
                st.session_state["downloads_rows"] = rows
                st.session_state.pop("download_request_parser_result", None)
                st.session_state.pop("download_request_parser_target_index", None)
                st.success(f"已回填到第 {target_index + 1} 行")
                st.rerun()

    with st.expander("预览第一行请求占位符替换结果", expanded=False):
        if st.button("生成预览", key="preview_dl_data_tokens"):
            try:
                first_row = download_rows_normalized[0] if download_rows_normalized else {}
                dl_payload = {
                    "url": first_row.get("url", ""),
                    "headers": json.loads(first_row.get("headers_json", "{}") or "{}"),
                    "data": json.loads(first_row.get("data_json", "{}") or "{}"),
                    "raw_body": first_row.get("raw_body", ""),
                }
                resolved_data, used_replacements = resolve_placeholder_preview(dl_payload)
                st.info(
                    "当前时间预览："
                    + ", ".join([f"{k} => {v}" for k, v in used_replacements.items()])
                )
                st.code(json.dumps(resolved_data, ensure_ascii=False, indent=2), language="json")
            except json.JSONDecodeError as exc:
                st.error(f"请求参数不是合法 JSON，无法预览: {exc}")

    # 比对参数
    st.markdown("**比对参数**")
    cmp = default.get("compare", {})

    download_name_options = [
        (row.get("name") or "").strip()
        for row in download_rows_normalized
        if (row.get("name") or "").strip()
    ]
    use_multi_report = len(download_name_options) > 1 or has_multi_config
    cmp_btn_col1, cmp_btn_col2 = st.columns([1, 5])
    default_compare_download = download_name_options[0] if download_name_options else ""
    if cmp_btn_col1.button("新增比对行"):
        rows = non_empty_compare_rows(st.session_state["compare_rows"])
        rows.append(empty_compare_row(default_compare_download))
        st.session_state["compare_rows"] = rows
        st.rerun()
    if cmp_btn_col2.button("清理比对空白行"):
        rows = non_empty_compare_rows(st.session_state["compare_rows"])
        st.session_state["compare_rows"] = rows or [empty_compare_row(default_compare_download)]
        st.rerun()
    compare_rows = st.data_editor(
        st.session_state["compare_rows"],
        key="compare_rows_editor",
        num_rows="fixed",
        width="stretch",
        column_config={
            "download_name": st.column_config.SelectboxColumn(
                "对应下载标识",
                options=download_name_options,
                required=use_multi_report,
                disabled=not use_multi_report,
            ),
            "name": st.column_config.TextColumn(
                "映射备注（可选）",
                help="只是备注名，方便在日志/结果中识别这一条映射；真正匹配靠“对应下载标识 + 源 sheet + 模板 sheet”。",
            ),
            "new_sheet_name": st.column_config.TextColumn("源 sheet", required=True),
            "template_sheet_name": st.column_config.TextColumn("模板 sheet", required=True),
            "header_row": st.column_config.NumberColumn(
                "表头行（可空）",
                min_value=1,
                step=1,
                help="源 sheet 中哪一行是字段名/列名。不填默认第 1 行。",
            ),
            "ignore_columns": st.column_config.TextColumn(
                "忽略列（可空，逗号分隔）",
                help="这些列不参与比对，例如更新时间、备注。填写列名，多个用英文逗号分隔。",
            ),
            "key_columns": st.column_config.TextColumn(
                "主键列（可空，逗号分隔）",
                help="用于匹配同一条记录的列，例如号码、地市、日期。为空时按行顺序比对。",
            ),
        },
    )
    template_update_default = default.get("template_update") or {}
    update_condition_options = ["all_changed", "any_changed"]
    write_sheets_options = ["changed", "all_compared"]
    wait_cfg = task_default.get("wait_for_change") or {}
    same_action_default = same_action_from_config(template_update_default, wait_cfg)
    same_action_value = st.radio(
        "比对结果 same 时怎么处理",
        options=SAME_ACTION_OPTIONS,
        index=SAME_ACTION_OPTIONS.index(same_action_default),
        horizontal=True,
        help=(
            "等待数据变化后发送：same 后 sleep 并重试；"
            "直接发送当前通报：same 后也生成临时模板并发送，发送成功后提交到正式模板；"
            "直接结束：same 后不发送。"
        ),
    )
    send_when_same_value = same_action_value == "直接发送当前通报"
    update_condition_value = template_update_default.get("update_condition", "any_changed")
    if update_condition_value not in update_condition_options:
        update_condition_value = "any_changed"
    write_sheets_value = template_update_default.get("write_sheets", "changed")
    if write_sheets_value not in write_sheets_options:
        write_sheets_value = "changed"
    if send_when_same_value:
        write_sheets_value = "all_compared"
        st.caption("提示：same 时会写入所有参与比对的 sheet，生成临时模板并发送；发送成功后提交到正式模板。")
    else:
        update_col1, update_col2 = st.columns(2)
        with update_col1:
            update_condition_value = st.selectbox(
                "数据变化判断条件",
                options=update_condition_options,
                index=update_condition_options.index(update_condition_value),
                help="all_changed 表示所有参与比较的 sheet 都变化才更新；any_changed 表示任一 sheet 变化即更新。",
            )
        with update_col2:
            write_sheets_value = st.selectbox(
                "数据变化时写入范围",
                options=write_sheets_options,
                index=write_sheets_options.index(write_sheets_value),
                help="changed 只写变化 sheet；all_compared 写所有参与比较的 sheet。",
            )
    if not send_when_same_value:
        st.caption(
            "提示：数据变化判断条件决定 changed/same；数据变化时写入范围决定更新模板时写哪些 sheet。"
        )

    with st.expander("高级 JSON 预览", expanded=False):
        preview_downloads, preview_download_errors = form_rows_to_downloads(
            apply_default_stage_to_rows(downloads_rows, dl_stage)
        )
        if use_multi_report:
            preview_compare_sources, preview_compare_errors = form_rows_to_compare_sources(compare_rows)
            preview_payload = {"downloads": preview_downloads, "compare_sources": preview_compare_sources}
        else:
            preview_sheet_mappings, preview_compare_errors = form_rows_to_sheet_mappings(compare_rows)
            preview_payload = {"download": preview_downloads[0] if preview_downloads else {}, "compare": {"sheet_mappings": preview_sheet_mappings}}
        preview_payload["template_update"] = {
            "update_condition": update_condition_value,
            "write_sheets": write_sheets_value,
            "send_when_same": bool(send_when_same_value),
        }
        if preview_download_errors or preview_compare_errors:
            for error in preview_download_errors + preview_compare_errors:
                st.warning(error)
        st.code(json.dumps(preview_payload, ensure_ascii=False, indent=2), language="json")

    # 发送参数
    st.markdown("**发送参数**")
    snd = default.get("send", {})
    snd_name = st.text_input("Workbook 名称", value=snd.get("workbook_name", name))
    snd_webhook_url = st.text_input(
        "企业微信 Webhook URL",
        value=snd.get("webhook_url", ""),
        placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...",
        help="填写后会覆盖 config/modules/wecom_sender.json 中的默认 webhook_url。",
    )
    send_btn_col1, send_btn_col2 = st.columns([1, 5])
    if send_btn_col1.button("新增发送行"):
        rows = non_empty_send_rows(st.session_state["send_rows"])
        rows.append(empty_send_row())
        st.session_state["send_rows"] = rows
        st.rerun()
    if send_btn_col2.button("清理发送空白行"):
        rows = non_empty_send_rows(st.session_state["send_rows"])
        st.session_state["send_rows"] = rows or [empty_send_row()]
        st.rerun()
    send_rows = st.data_editor(
        st.session_state["send_rows"],
        key="send_rows_editor",
        num_rows="fixed",
        width="stretch",
        column_config={
            "type": st.column_config.SelectboxColumn("类型", options=["image", "text"], required=True),
            "sheet": st.column_config.TextColumn("sheet", required=True),
            "text_mode": st.column_config.SelectboxColumn(
                "文本模式",
                options=["", "used_range", "explicit_range", "current_region"],
                help="仅 text 类型使用；复杂参数可写在额外配置 JSON。",
            ),
            "extra_json": st.column_config.TextColumn("额外配置 JSON", width="medium"),
        },
    )

    # 等待重试参数（写入 task 配置）
    st.markdown("**等待重试（wait_for_change）**")
    wait_enabled = same_action_value == "等待数据变化后发送"
    if wait_enabled:
        wait_poll_seconds = st.number_input(
            "重试间隔秒数（poll_interval_seconds）",
            min_value=1,
            value=int(wait_cfg.get("poll_interval_seconds", 300)),
            step=10,
        )
        wait_max_minutes = st.number_input(
            "最大等待分钟（max_wait_minutes）",
            min_value=1,
            value=int(wait_cfg.get("max_wait_minutes", 180)),
            step=10,
        )
    else:
        wait_poll_seconds = int(wait_cfg.get("poll_interval_seconds", 300))
        wait_max_minutes = int(wait_cfg.get("max_wait_minutes", 180))
        st.caption("当前 same 处理方式不会执行等待重试，下面的间隔和超时参数会保留但不生效。")

    # 定时设置
    st.markdown("**定时部署**")
    enable_cron = st.checkbox("启用定时", value=False)
    cron_expr = ""
    cron_timezone = "Asia/Shanghai"
    if enable_cron:
        cron_col1, cron_col2 = st.columns([2, 1])
        with cron_col1:
            cron_expr = st.text_input(
                "Cron 表达式",
                value="0 8 * * 1-5",
                help="例：0 9-18 * * * 表示每天 9 点到 18 点每小时执行一次",
            )
        with cron_col2:
            cron_timezone = st.text_input(
                "时区",
                value="Asia/Shanghai",
                help="中国大陆使用 Asia/Shanghai。设置后 Cron 按北京时间解释，不再按 UTC 偏移。",
            )

    btn_col1, btn_col2, btn_col3 = st.columns(3)
    draft_clicked = btn_col1.button("保存草稿")
    save_clicked = btn_col2.button("保存配置", type="primary")
    deploy_clicked = btn_col3.button("保存并部署")

    if draft_clicked:
        draft_name = name.strip() or f"未命名草稿_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        draft_downloads = form_rows_to_download_drafts(downloads_rows, dl_stage)
        draft_primary_download = draft_downloads[0] if draft_downloads else {
            "name": "",
            "stage": dl_stage,
            "method": "POST",
            "url": "",
            "headers": {},
            "body_type": "form",
            "data": {},
        }
        draft_compare_sources = form_rows_to_compare_source_drafts(compare_rows)
        draft_compare_mappings = []
        if not use_multi_report:
            draft_compare_sources = []
            draft_compare_mappings = []
            for row in normalize_table_rows(compare_rows):
                mapping = {
                    "name": (row.get("name") or "").strip(),
                    "new_sheet_name": (row.get("new_sheet_name") or "").strip(),
                    "template_sheet_name": (row.get("template_sheet_name") or "").strip(),
                    "header_row": parse_optional_int(row.get("header_row")),
                    "ignore_columns": [c.strip() for c in (row.get("ignore_columns") or "").split(",") if c.strip()],
                    "key_columns": [c.strip() for c in (row.get("key_columns") or "").split(",") if c.strip()],
                }
                if any(value not in ("", [], None) for value in mapping.values()):
                    draft_compare_mappings.append(mapping)
        draft_send_items = form_rows_to_send_item_drafts(send_rows)
        template_update_parsed = {
            "update_condition": update_condition_value,
            "write_sheets": write_sheets_value,
            "send_when_same": bool(send_when_same_value),
        }
        report_cfg = build_report_config_payload(
            draft_name,
            template_path,
            draft_primary_download,
            draft_downloads,
            draft_compare_mappings,
            draft_compare_sources,
            snd_webhook_url,
            snd_name,
            draft_send_items,
            template_update_parsed,
            use_multi_report,
            download_mode,
            draft=True,
        )
        report_path = REPORTS_DIR / f"{draft_name}.json"
        save_json(report_path, report_cfg)
        st.success(f"草稿已保存：{report_path.name}。草稿不会自动部署，补全后再点“保存配置”或“保存并部署”。")

    if save_clicked or deploy_clicked:
        errors = []
        downloads_parsed, download_form_errors = form_rows_to_downloads(
            apply_default_stage_to_rows(downloads_rows, dl_stage)
        )
        errors.extend(download_form_errors)
        if not downloads_parsed:
            errors.append("至少需要一个下载报表")
        primary_download = downloads_parsed[0] if downloads_parsed else {
            "name": name,
            "stage": "report_analysis",
            "url": "",
            "data": {},
        }
        if use_multi_report:
            compare_sources_parsed, compare_form_errors = form_rows_to_compare_sources(compare_rows)
            errors.extend(compare_form_errors)
            if not compare_sources_parsed:
                errors.append("多报表模式下至少需要一条 sheet 映射")
            cmp_sheet_mappings_parsed = []
        else:
            downloads_parsed = []
            compare_sources_parsed = []
            cmp_sheet_mappings_parsed, compare_form_errors = form_rows_to_sheet_mappings(compare_rows)
            errors.extend(compare_form_errors)
        snd_items_parsed, send_form_errors = form_rows_to_send_items(send_rows)
        errors.extend(send_form_errors)
        template_update_parsed = {
            "update_condition": update_condition_value,
            "write_sheets": write_sheets_value,
            "send_when_same": bool(send_when_same_value),
        }

        if not name:
            errors.append("通报名称不能为空")

        if errors:
            for e in errors:
                st.error(e)
        else:
            report_cfg = build_report_config_payload(
                name,
                template_path,
                primary_download,
                downloads_parsed,
                cmp_sheet_mappings_parsed,
                compare_sources_parsed,
                snd_webhook_url,
                snd_name,
                snd_items_parsed,
                template_update_parsed,
                use_multi_report,
                download_mode,
                draft=False,
            )
            report_path = REPORTS_DIR / f"{name}.json"
            save_json(report_path, report_cfg)
            task_cfg = build_task_config(name, str(report_path.relative_to(PROJECT_DIR)).replace("\\", "/"), download_name=primary_download.get("name"))
            task_cfg["wait_for_change"] = {
                "enabled": bool(wait_enabled),
                "poll_interval_seconds": int(wait_poll_seconds),
                "max_wait_minutes": int(wait_max_minutes),
            }
            task_path = task_config_path(name)
            save_json(task_path, task_cfg)
            st.success("配置已保存")

            if deploy_clicked:
                ok, output = deploy(
                    name,
                    task_path,
                    cron_expr if enable_cron else None,
                    cron_timezone if enable_cron else None,
                )
                if ok:
                    st.success(f"部署成功：notify-{name.replace(' ', '_')}")
                else:
                    st.error("部署失败")
                    st.code(output)


