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
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
TASKS_DIR.mkdir(parents=True, exist_ok=True)

WORK_POOL = "default-agent-pool"
FLOW_ENTRYPOINT = "flows/notify_single_flow.py:auto_notify_flow"
DEFAULT_STAGE_OPTIONS = ["report_analysis", "smart_ops", "data_market"]
COOKIE_DUMP_PATH = PROJECT_DIR / "runtime" / "cookies" / "cookie_dump.json"


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


def deploy(report_name: str, task_cfg_path: Path, cron: str | None):
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


def download_to_form_row(item: dict) -> dict:
    return {
        "name": item.get("name", ""),
        "stage": item.get("stage", "report_analysis"),
        "url": item.get("url", ""),
        "data_json": json.dumps(item.get("data", {}), ensure_ascii=False, indent=2),
    }


def normalize_table_rows(rows) -> list[dict]:
    if rows is None:
        return []
    if hasattr(rows, "to_dict"):
        return rows.to_dict("records")
    return [dict(row) for row in rows]


def downloads_to_form_rows(downloads: list[dict]) -> list[dict]:
    rows = [download_to_form_row(item or {}) for item in downloads]
    return rows or [download_to_form_row({})]


def form_rows_to_downloads(rows: list[dict]) -> tuple[list[dict], list[str]]:
    rows = normalize_table_rows(rows)
    downloads = []
    errors = []
    for index, row in enumerate(rows, start=1):
        name = (row.get("name") or "").strip()
        stage = (row.get("stage") or "").strip()
        url = (row.get("url") or "").strip()
        data_raw = row.get("data_json") or "{}"
        if not any([name, stage, url, data_raw.strip() not in {"", "{}"}]):
            continue
        try:
            data = json.loads(data_raw or "{}")
            if not isinstance(data, dict):
                errors.append(f"downloads 第 {index} 行 data_json 必须是对象 JSON")
                data = {}
        except json.JSONDecodeError as exc:
            errors.append(f"downloads 第 {index} 行 data_json 格式错误: {exc}")
            data = {}
        if not name:
            errors.append(f"downloads 第 {index} 行缺少 name")
        if not stage:
            errors.append(f"downloads 第 {index} 行缺少 stage")
        if not url:
            errors.append(f"downloads 第 {index} 行缺少 url")
        downloads.append({
            "name": name,
            "stage": stage,
            "url": url,
            "data": data,
        })
    return downloads, errors


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
                "header_row": int(mapping.get("header_row") or 1),
                "ignore_columns": ",".join(mapping.get("ignore_columns") or []),
                "key_columns": ",".join(mapping.get("key_columns") or []),
            })
    return rows or [{
        "download_name": "",
        "name": "",
        "new_sheet_name": "",
        "template_sheet_name": "",
        "header_row": 1,
        "ignore_columns": "",
        "key_columns": "",
    }]


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
            "header_row": int(mapping.get("header_row") or 1),
            "ignore_columns": ",".join(mapping.get("ignore_columns") or []),
            "key_columns": ",".join(mapping.get("key_columns") or []),
        })
    return rows or [{
        "download_name": download_name,
        "name": "",
        "new_sheet_name": "",
        "template_sheet_name": "",
        "header_row": 1,
        "ignore_columns": "",
        "key_columns": "",
    }]


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
            "header_row": int(row.get("header_row") or 1),
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
            "header_row": int(row.get("header_row") or 1),
            "ignore_columns": [c.strip() for c in (row.get("ignore_columns") or "").split(",") if c.strip()],
            "key_columns": [c.strip() for c in (row.get("key_columns") or "").split(",") if c.strip()],
        }
        grouped.setdefault(download_name, []).append(mapping)
    return [
        {"download_name": download_name, "sheet_mappings": mappings}
        for download_name, mappings in grouped.items()
    ], errors


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
    return rows or [{
        "type": "image",
        "sheet": "",
        "text_mode": "",
        "extra_json": "{}",
    }]


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


def resolve_placeholder_preview(payload):
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    replacements = {
        "${today}": now.strftime("%Y-%m-%d"),
        "${yesterday}": yesterday.strftime("%Y-%m-%d"),
        "${yesterday_yyyymmdd}": yesterday.strftime("%Y%m%d"),
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

col_list, col_edit = st.columns([1, 2])

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
                "write_sheets": "changed"
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
    name = st.text_input("报表名称", value=default.get("name", ""))
    template_path = st.text_input("模板路径", value=default.get("template_path", "templates/"))

    # 下载参数
    st.markdown("**下载报表**")
    dl = default.get("download", {})
    stage_options = list_stage_options()
    current_stage = dl.get("stage", "report_analysis")
    if current_stage not in stage_options:
        stage_options = [current_stage] + stage_options
    dl_stage = st.selectbox(
        "Cookie Stage",
        options=stage_options,
        index=stage_options.index(current_stage),
        help=(
            "用于指定下载报表时使用哪一组登录 Cookie。"
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

    st.caption("一行就是一个下载报表；只有一行时就是单表，多加几行就是多表。")
    downloads_rows = st.data_editor(
        st.session_state["downloads_rows"],
        key="downloads_rows_editor",
        num_rows="dynamic",
        width="stretch",
        column_config={
            "name": st.column_config.TextColumn("下载名称", required=True),
            "stage": st.column_config.SelectboxColumn("Cookie Stage", options=stage_options, required=True),
            "url": st.column_config.TextColumn("下载 URL", required=True),
            "data_json": st.column_config.TextColumn("请求 data JSON", width="large"),
        },
    )
    download_rows_normalized = normalize_table_rows(downloads_rows)

    with st.expander("请求载体转 JSON（URL + 载体原文）", expanded=False):
        target_options = [
            f"{index + 1}. {(row.get('name') or '未命名下载')}"
            for index, row in enumerate(download_rows_normalized)
        ] or ["1. 未命名下载"]
        target_label = st.selectbox("回填到下载行", options=target_options)
        target_index = target_options.index(target_label)
        target_download_row = download_rows_normalized[target_index] if download_rows_normalized else {}
        convert_url = st.text_input("请求 URL（可选）", value=target_download_row.get("url", ""), help="若 URL 有 query 参数，会自动合并到 data")
        raw_payload = st.text_area(
            "请求载体原文",
            value="",
            height=120,
            help="支持 JSON、a=1&b=2、或按行 key=value / key: value",
        )
        if st.button("转换并回填到 data 参数"):
            try:
                converted = parse_payload_text_to_json(raw_payload)
                converted = merge_url_query_payload(convert_url, converted)
                rows = normalize_table_rows(downloads_rows)
                if not rows:
                    rows = downloads_to_form_rows([{}])
                while len(rows) <= target_index:
                    rows.append(download_to_form_row({}))
                rows[target_index]["url"] = convert_url or rows[target_index].get("url", "")
                rows[target_index]["data_json"] = json.dumps(converted, ensure_ascii=False, indent=2)
                st.session_state["downloads_rows"] = rows
                st.success(f"转换成功，已回填到第 {target_index + 1} 行")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    st.caption(
        "占位符支持：`${today}`(YYYY-MM-DD), `${yesterday}`(前一天 YYYY-MM-DD), "
        "`${yesterday_yyyymmdd}`(前一天 YYYYMMDD), `${hour}`(0-23), `${hour2}`(00-23)"
    )
    if st.button("预览第一行 data 占位符替换结果", key="preview_dl_data_tokens"):
        try:
            first_row = download_rows_normalized[0] if download_rows_normalized else {}
            dl_data_obj = json.loads(first_row.get("data_json", "{}") or "{}")
            resolved_data, used_replacements = resolve_placeholder_preview(dl_data_obj)
            st.info(
                "当前时间预览："
                + ", ".join([f"{k} => {v}" for k, v in used_replacements.items()])
            )
            st.code(json.dumps(resolved_data, ensure_ascii=False, indent=2), language="json")
        except json.JSONDecodeError as exc:
            st.error(f"data 参数不是合法 JSON，无法预览: {exc}")

    # 比对参数
    st.markdown("**比对参数**")
    cmp = default.get("compare", {})

    download_name_options = [
        (row.get("name") or "").strip()
        for row in download_rows_normalized
        if (row.get("name") or "").strip()
    ]
    use_multi_report = len(download_name_options) > 1 or has_multi_config
    compare_rows = st.data_editor(
        st.session_state["compare_rows"],
        key="compare_rows_editor",
        num_rows="dynamic",
        width="stretch",
        column_config={
            "download_name": st.column_config.SelectboxColumn(
                "对应下载报表",
                options=download_name_options,
                required=use_multi_report,
                disabled=not use_multi_report,
            ),
            "name": st.column_config.TextColumn("映射名称"),
            "new_sheet_name": st.column_config.TextColumn("源 sheet", required=True),
            "template_sheet_name": st.column_config.TextColumn("模板 sheet", required=True),
            "header_row": st.column_config.NumberColumn("表头行", min_value=1, step=1),
            "ignore_columns": st.column_config.TextColumn("忽略列（逗号分隔）"),
            "key_columns": st.column_config.TextColumn("主键列（逗号分隔）"),
        },
    )
    template_update_default = default.get("template_update") or {}
    update_condition_options = ["all_changed", "any_changed"]
    write_sheets_options = ["changed", "all_compared"]
    update_col1, update_col2 = st.columns(2)
    with update_col1:
        update_condition_value = st.selectbox(
            "更新条件",
            options=update_condition_options,
            index=update_condition_options.index(template_update_default.get("update_condition", "any_changed"))
            if template_update_default.get("update_condition", "any_changed") in update_condition_options
            else 1,
            help="all_changed 表示所有参与比较的 sheet 都变化才更新；any_changed 表示任一 sheet 变化即更新。",
        )
    with update_col2:
        write_sheets_value = st.selectbox(
            "写入范围",
            options=write_sheets_options,
            index=write_sheets_options.index(template_update_default.get("write_sheets", "changed"))
            if template_update_default.get("write_sheets", "changed") in write_sheets_options
            else 0,
            help="changed 只写变化 sheet；all_compared 写所有参与比较的 sheet。",
        )

    with st.expander("高级 JSON 预览", expanded=False):
        preview_downloads, preview_download_errors = form_rows_to_downloads(downloads_rows)
        if use_multi_report:
            preview_compare_sources, preview_compare_errors = form_rows_to_compare_sources(compare_rows)
            preview_payload = {"downloads": preview_downloads, "compare_sources": preview_compare_sources}
        else:
            preview_sheet_mappings, preview_compare_errors = form_rows_to_sheet_mappings(compare_rows)
            preview_payload = {"download": preview_downloads[0] if preview_downloads else {}, "compare": {"sheet_mappings": preview_sheet_mappings}}
        preview_payload["template_update"] = {
            "update_condition": update_condition_value,
            "write_sheets": write_sheets_value,
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
    send_rows = st.data_editor(
        st.session_state["send_rows"],
        key="send_rows_editor",
        num_rows="dynamic",
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
    wait_cfg = task_default.get("wait_for_change") or {}
    wait_enabled = st.checkbox("启用 same 自动重试", value=bool(wait_cfg.get("enabled", True)))
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

    # 定时设置
    st.markdown("**定时部署**")
    enable_cron = st.checkbox("启用定时", value=False)
    cron_expr = ""
    if enable_cron:
        cron_expr = st.text_input("Cron 表达式", value="0 8 * * 1-5", help="例：0 8 * * 1-5 表示工作日早8点")

    btn_col1, btn_col2 = st.columns(2)
    save_clicked = btn_col1.button("保存配置", type="primary")
    deploy_clicked = btn_col2.button("保存并部署")

    if save_clicked or deploy_clicked:
        errors = []
        downloads_parsed, download_form_errors = form_rows_to_downloads(downloads_rows)
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
        }

        if not name:
            errors.append("报表名称不能为空")

        if errors:
            for e in errors:
                st.error(e)
        else:
            report_cfg = {
                "name": name,
                "template_path": template_path,
                "download": primary_download,
                "compare": {
                    "sheet_mappings": cmp_sheet_mappings_parsed,
                    "header_row": 1,
                    "ignore_columns": [],
                    "key_columns": [],
                },
                "send": {
                    "webhook_url": snd_webhook_url.strip(),
                    "workbook_name": snd_name,
                    "items": snd_items_parsed,
                },
                "template_update": template_update_parsed,
            }
            if use_multi_report:
                report_cfg["downloads"] = downloads_parsed
                report_cfg["compare_sources"] = compare_sources_parsed
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
                ok, output = deploy(name, task_path, cron_expr if enable_cron else None)
                if ok:
                    st.success(f"部署成功：notify-{name.replace(' ', '_')}")
                else:
                    st.error("部署失败")
                    st.code(output)
