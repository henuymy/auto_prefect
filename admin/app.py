"""Streamlit admin UI for managing report configs and Prefect deployments."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
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
    result = subprocess.run(cmd, cwd=str(PROJECT_DIR), capture_output=True, text=True)
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


def resolve_placeholder_preview(payload):
    now = datetime.now()
    replacements = {
        "${today}": now.strftime("%Y-%m-%d"),
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
    st.markdown("**下载参数**")
    dl = default.get("download", {})
    dl_name = st.text_input("下载任务名称", value=dl.get("name", name))
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

    dl_url = st.text_input("下载 URL", value=dl.get("url", ""))
    selected_marker = selected if not is_new else "__new__"
    dl_data_default = json.dumps(dl.get("data", {}), ensure_ascii=False, indent=2)
    if st.session_state.get("dl_data_marker") != selected_marker:
        st.session_state["dl_data_marker"] = selected_marker
        st.session_state["dl_data_input"] = dl_data_default

    snd_items_default = json.dumps(default.get("send", {}).get("items", []), ensure_ascii=False, indent=2)
    if st.session_state.get("snd_items_marker") != selected_marker:
        st.session_state["snd_items_marker"] = selected_marker
        st.session_state["snd_items_input"] = snd_items_default

    with st.expander("请求载体转 JSON（URL + 载体原文）", expanded=False):
        convert_url = st.text_input("请求 URL（可选）", value=dl_url, help="若 URL 有 query 参数，会自动合并到 data")
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
                st.session_state["dl_data_input"] = json.dumps(converted, ensure_ascii=False, indent=2)
                st.success("转换成功，已回填到 data 参数")
            except ValueError as exc:
                st.error(str(exc))

    st.markdown("`data 参数 (JSON)`")
    data_toolbar_col1, data_toolbar_col2 = st.columns([1, 5])
    with data_toolbar_col1:
        if st.button("格式化 data", key="format_dl_data"):
            format_json_text_in_state("dl_data_input", "data 参数")
    with data_toolbar_col2:
        st.caption("支持对象 JSON，用于下载请求载体。")
    dl_data = st.text_area(
        "data 参数 (JSON) 编辑器",
        key="dl_data_input",
        height=220,
        label_visibility="collapsed",
        placeholder='{\n  "key": "value"\n}',
    )
    st.caption("占位符支持：`${today}`(YYYY-MM-DD), `${hour}`(0-23), `${hour2}`(00-23)")
    if st.button("预览 data 占位符替换结果", key="preview_dl_data_tokens"):
        try:
            dl_data_obj = json.loads(st.session_state.get("dl_data_input", "{}") or "{}")
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
    cmp_header_row = st.number_input("表头行号", value=int(cmp.get("header_row", 1)), min_value=1)
    cmp_sheet_mappings = st.text_area("sheet_mappings (JSON)", value=json.dumps(cmp.get("sheet_mappings", []), ensure_ascii=False, indent=2), height=80)
    cmp_ignore = st.text_input("忽略列 (逗号分隔)", value=",".join(cmp.get("ignore_columns") or []))
    cmp_key = st.text_input("主键列 (逗号分隔)", value=",".join(cmp.get("key_columns") or []))

    # 发送参数
    st.markdown("**发送参数**")
    snd = default.get("send", {})
    snd_name = st.text_input("Workbook 名称", value=snd.get("workbook_name", name))
    st.markdown("`items (JSON)`")
    items_toolbar_col1, items_toolbar_col2 = st.columns([1, 5])
    with items_toolbar_col1:
        if st.button("格式化 items", key="format_snd_items"):
            format_json_text_in_state("snd_items_input", "items 参数")
    with items_toolbar_col2:
        st.caption("支持数组 JSON，每项可配置 image/text、sheet、text 规则。")
    snd_items = st.text_area(
        "items (JSON) 编辑器",
        key="snd_items_input",
        height=180,
        label_visibility="collapsed",
        placeholder='[\n  {\n    "type": "image",\n    "sheet": "通报"\n  }\n]',
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
        try:
            dl_data_parsed = json.loads(dl_data)
        except json.JSONDecodeError:
            errors.append("data 参数 JSON 格式错误")
            dl_data_parsed = {}
        try:
            cmp_sheet_mappings_parsed = json.loads(cmp_sheet_mappings)
        except json.JSONDecodeError:
            errors.append("sheet_mappings JSON 格式错误")
            cmp_sheet_mappings_parsed = []
        try:
            snd_items_parsed = json.loads(snd_items)
        except json.JSONDecodeError:
            errors.append("items JSON 格式错误")
            snd_items_parsed = []

        if not name:
            errors.append("报表名称不能为空")

        if errors:
            for e in errors:
                st.error(e)
        else:
            report_cfg = {
                "name": name,
                "template_path": template_path,
                "download": {
                    "name": dl_name,
                    "stage": dl_stage,
                    "url": dl_url,
                    "data": dl_data_parsed,
                },
                "compare": {
                    "sheet_mappings": cmp_sheet_mappings_parsed,
                    "header_row": cmp_header_row,
                    "ignore_columns": [c.strip() for c in cmp_ignore.split(",") if c.strip()],
                    "key_columns": [c.strip() for c in cmp_key.split(",") if c.strip()],
                },
                "send": {
                    "workbook_name": snd_name,
                    "items": snd_items_parsed,
                },
            }
            report_path = REPORTS_DIR / f"{name}.json"
            save_json(report_path, report_cfg)
            task_cfg = build_task_config(name, str(report_path.relative_to(PROJECT_DIR)).replace("\\", "/"), download_name=dl_name)
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
