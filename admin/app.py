"""Streamlit admin UI for managing report configs and Prefect deployments."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import streamlit as st

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_DIR / "config" / "reports"
TASKS_DIR = PROJECT_DIR / "config" / "tasks"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
TASKS_DIR.mkdir(parents=True, exist_ok=True)

WORK_POOL = "default-agent-pool"
FLOW_ENTRYPOINT = "flows/notify_single_flow.py:auto_notify_flow"


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

    # 基本信息
    name = st.text_input("报表名称", value=default.get("name", ""))
    template_path = st.text_input("模板路径", value=default.get("template_path", "templates/"))

    # 下载参数
    st.markdown("**下载参数**")
    dl = default.get("download", {})
    dl_name = st.text_input("下载任务名称", value=dl.get("name", name))
    dl_stage = st.text_input("Cookie Stage", value=dl.get("stage", "report_analysis"))
    dl_data = st.text_area("data 参数 (JSON)", value=json.dumps(dl.get("data", {}), ensure_ascii=False, indent=2), height=200)

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
    snd_items = st.text_area("items (JSON)", value=json.dumps(snd.get("items", []), ensure_ascii=False, indent=2), height=120)

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

