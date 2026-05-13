"""NiceGUI report configuration admin."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import nicegui.run as nicegui_run
from nicegui import ui

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from .config_store import (
    PROJECT_DIR, list_report_names, list_templates, load_report, load_stage_titles,
    save_draft, save_report, save_task_config,
)
from .deploy import deploy_report
from .download_ui import render_download_cards
from .models import empty_config, empty_send_item, json_text, normalize_config
from .serializers import config_to_save_payload

state = {
    "selected": "",
    "config": normalize_config(empty_config()),
    "dirty_token": 0,
}

_original_nicegui_setup = nicegui_run.setup

def _safe_nicegui_setup() -> None:
    try:
        _original_nicegui_setup()
    except PermissionError:
        nicegui_run.process_pool = None

nicegui_run.setup = _safe_nicegui_setup

def refresh() -> None:
    state["dirty_token"] += 1

def select_report(name: str) -> None:
    state["selected"] = name
    state["config"] = load_report(name)
    refresh()
    ui.navigate.reload()

def new_report() -> None:
    state["selected"] = ""
    state["config"] = normalize_config(empty_config())
    refresh()
    ui.navigate.reload()

def save_current(draft: bool = False, deploy: bool = False) -> None:
    try:
        payload = config_to_save_payload(state["config"])
        if not payload.get("name"):
            raise ValueError("通报名称不能为空")
        path = save_draft(payload) if draft else save_report(payload)
        if draft:
            ui.notify(f"草稿已保存：{path.name}", type="positive", position="top")
            return
        if deploy:
            task_path = save_task_config(payload)
            deployment = payload.get("deployment") or {}
            ok, output = deploy_report(
                PROJECT_DIR, payload["name"], task_path,
                cron=deployment.get("cron", "") if deployment.get("enabled") else "",
                timezone=deployment.get("timezone", "Asia/Shanghai"),
            )
            if ok:
                ui.notify("配置已保存并部署成功", type="positive", position="top")
            else:
                ui.notify("部署失败，请查看输出", type="negative", position="top")
                show_text_dialog("部署输出", output)
        else:
            ui.notify(f"配置已保存：{path.name}", type="positive", position="top")
        state["selected"] = payload["name"]
    except Exception as exc:
        ui.notify(f"保存失败: {exc}", type="negative", position="top")

def show_text_dialog(title: str, content: str) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-3/4 no-shadow border rounded-lg"):
        ui.label(title).classes("text-lg font-bold text-gray-800 mb-2")
        ui.textarea(value=content).props("readonly outlined").classes("w-full")
        with ui.row().classes("w-full justify-end mt-4"):
            ui.button("关闭", on_click=dialog.close).props("unelevated color=primary")
    dialog.open()

def section_title(text: str, icon: str) -> None:
    with ui.row().classes("items-center gap-2 mb-6 w-full pb-2 border-b border-gray-100"):
        ui.icon(icon, color="primary", size="sm")
        ui.label(text).classes("text-xl font-bold text-gray-800")

def render_header() -> None:
    with ui.header().classes("bg-white text-gray-800 border-b border-gray-200 items-center justify-between px-6 py-3").props("elevated=false"):
        with ui.row().classes("items-center gap-4"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("rocket_launch", size="md", color="primary")
                ui.label("自动化通报系统").classes("text-xl font-bold tracking-tight text-gray-900")
            
            ui.separator().props("vertical").classes("mx-2 h-8")
            
            report_names = list_report_names()
            selected = state.get("selected")
            if selected not in report_names:
                selected = None
            ui.select(options=report_names, value=selected, label="切换通报配置", on_change=lambda e: select_report(e.value) if e.value else None).classes("w-64").props("outlined dense bg-color=white")
            ui.button("新建", icon="add", on_click=new_report).props("flat color=primary")

        with ui.row().classes("gap-3 items-center"):
            ui.button("保存草稿", icon="save", on_click=lambda: save_current(draft=True)).props("outline color=grey-7")
            ui.button("保存并部署", icon="cloud_upload", on_click=lambda: save_current(deploy=True)).props("color=teal unelevated")
            ui.button("保存配置", icon="check_circle", on_click=lambda: save_current()).props("color=primary unelevated")

def render_base_panel(config: dict) -> None:
    section_title("基础属性", "info")
    with ui.grid(columns=2).classes("w-full gap-6"):
        ui.input("通报名称 (唯一标识)").bind_value(config, "name").classes("w-full").props("outlined bg-color=white")
        templates = list_templates()
        selected_template = config.get("template_path") or (templates[0] if templates else "")
        if templates:
            ui.select(templates, value=selected_template, label="正式基线模板文件").bind_value(config, "template_path").classes("w-full").props("outlined bg-color=white")
        else:
            ui.input("模板路径").bind_value(config, "template_path").classes("w-full").props("outlined bg-color=white")
            
    stage_titles = load_stage_titles()
    if stage_titles:
        with ui.expansion("可用 Cookie Stage 参考字典", icon="cookie").classes("w-full mt-6 bg-gray-50 border border-gray-200 rounded-lg no-shadow"):
            with ui.row().classes("gap-4 p-4"):
                for stage, title in stage_titles.items():
                    ui.chip(f"{stage}: {title}", icon="vpn_key", color="primary", text_color="white").props("outline")

def render_compare_panel(config: dict) -> None:
    with ui.row().classes("items-center justify-between w-full mb-4"):
        section_title("Excel 比对规则", "compare_arrows")
        ui.button("添加比对数据源", icon="add", on_click=lambda: add_compare()).props("outline color=primary size=sm")

    compare_sources = config.setdefault("compare_sources", [])
    download_names = [item.get("name") for item in config.get("downloads", []) if item.get("name")]

    def add_compare() -> None:
        compare_sources.append({
            "download_name": download_names[0] if download_names else "",
            "sheet_mappings": [{"name": "", "new_sheet_name": "", "template_sheet_name": "", "header_row": 1, "ignore_columns": [], "key_columns": []}]
        })
        ui.navigate.reload()

    if not compare_sources:
        with ui.card().classes("w-full p-8 items-center justify-center bg-gray-50 border border-dashed border-gray-300 no-shadow"):
            ui.label("暂无比对数据源，请点击右上角添加").classes("text-gray-400")

    for source_index, source in enumerate(compare_sources):
        with ui.card().classes("w-full p-6 mb-6 bg-white no-shadow border border-gray-200 rounded-lg"):
            with ui.row().classes("items-center justify-between w-full mb-4 pb-2 border-b border-gray-100"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("source", color="secondary")
                    ui.label(f"数据源 #{source_index + 1}").classes("text-lg font-bold text-gray-700")
                ui.button("移除数据源", icon="delete_outline", on_click=lambda i=source_index: (compare_sources.pop(i), ui.navigate.reload())).props("flat color=negative size=sm")
            
            ui.select(download_names or [source.get("download_name", "")], label="绑定下载项配置").bind_value(
                source, "download_name"
            ).classes("w-full max-w-md mb-6").props("outlined bg-color=white")
            
            mappings = source.setdefault("sheet_mappings", [])
            if not mappings:
                mappings.append({})
                
            ui.label("Sheet 映射明细").classes("text-sm font-bold text-gray-500 mb-2 uppercase tracking-wider")
            for mapping_index, mapping in enumerate(mappings):
                with ui.row().classes("w-full gap-4 items-start bg-gray-50 p-4 rounded border border-gray-100 mb-4"):
                    with ui.grid(columns=3).classes("flex-1 gap-4"):
                        ui.input("映射备注").bind_value(mapping, "name").props("outlined bg-color=white dense")
                        ui.input("接口源 Sheet 名").bind_value(mapping, "new_sheet_name").props("outlined bg-color=white dense")
                        ui.input("本地模板 Sheet 名").bind_value(mapping, "template_sheet_name").props("outlined bg-color=white dense")
                        ui.number("表头行号 (从1开始)", value=mapping.get("header_row", 1), min=1).bind_value(mapping, "header_row").props("outlined bg-color=white dense")
                        ui.input("忽略列 (逗号分隔)", value=",".join(mapping.get("ignore_columns") or [])).on(
                            "blur", lambda e, m=mapping: m.update({"ignore_columns": [x.strip() for x in (e.sender.value or "").split(",") if x.strip()]})
                        ).props("outlined bg-color=white dense")
                        ui.input("主键列 (逗号分隔)", value=",".join(mapping.get("key_columns") or [])).on(
                            "blur", lambda e, m=mapping: m.update({"key_columns": [x.strip() for x in (e.sender.value or "").split(",") if x.strip()]})
                        ).props("outlined bg-color=white dense")
                    ui.button(icon="close", on_click=lambda s=source, mi=mapping_index: (s["sheet_mappings"].pop(mi), ui.navigate.reload())).props("flat round color=negative size=sm").classes("mt-1")
            
            ui.button("添加映射关系", icon="add", on_click=lambda s=source: (s["sheet_mappings"].append({}), ui.navigate.reload())).props("flat color=primary size=sm")

def render_send_panel(config: dict) -> None:
    with ui.row().classes("items-center justify-between w-full mb-4"):
        section_title("企微推送配置", "send")
        ui.button("添加发送图文", icon="add", on_click=lambda: (send["items"].append(empty_send_item()), ui.navigate.reload())).props("outline color=primary size=sm")

    send = config.setdefault("send", {})
    send.setdefault("items", [empty_send_item()])
    
    with ui.card().classes("w-full p-6 mb-6 bg-white no-shadow border border-gray-200 rounded-lg"):
        ui.label("全局设置").classes("text-sm font-bold text-gray-500 mb-4 uppercase tracking-wider")
        with ui.grid(columns=2).classes("w-full gap-6"):
            ui.input("目标 Workbook 名称").bind_value(send, "workbook_name").classes("w-full").props("outlined bg-color=white")
            ui.input("企微机器人 Webhook URL").bind_value(send, "webhook_url").classes("w-full").props("outlined bg-color=white")

    ui.label("发送内容序列").classes("text-sm font-bold text-gray-500 mb-2 uppercase tracking-wider")
    for index, item in enumerate(send["items"]):
        with ui.row().classes("w-full gap-4 items-center bg-white p-4 rounded border border-gray-200 mb-4 no-shadow hover:shadow-md transition-shadow"):
            ui.avatar(str(index + 1), color="primary", text_color="white", size="sm")
            with ui.grid(columns=3).classes("flex-1 gap-4"):
                ui.select({"image": "图片截图", "text": "纯文本"}, label="推送类型").bind_value(item, "type").props("outlined bg-color=white dense")
                ui.input("目标 Sheet").bind_value(item, "sheet").props("outlined bg-color=white dense")
                text_cfg = item.setdefault("text", {})
                ui.select({"none": "默认", "used_range": "使用范围读取"}, value=text_cfg.get("mode", "none"), label="文本读取模式").bind_value(text_cfg, "mode").props("outlined bg-color=white dense")
            ui.button(icon="delete", on_click=lambda i=index: (send["items"].pop(i), ui.navigate.reload())).props("flat round color=negative")

def render_advanced_panel(config: dict) -> None:
    section_title("高级调度与策略", "settings")
    
    with ui.grid(columns=2).classes("w-full gap-6"):
        with ui.card().classes("w-full p-6 bg-white no-shadow border border-gray-200 rounded-lg"):
            ui.label("模板更新策略").classes("text-lg font-bold text-gray-700 mb-4")
            update = config.setdefault("template_update", {})
            ui.select({"any_changed": "任何 Sheet 变动即更新", "all_changed": "所有 Sheet 变动才更新"}, label="触发条件").bind_value(update, "update_condition").props("outlined bg-color=white mb-4").classes("w-full")
            ui.select({"changed": "仅写入变动的 Sheet", "all_compared": "写入所有参与比对的 Sheet"}, label="数据写入范围").bind_value(update, "write_sheets").props("outlined bg-color=white mb-4").classes("w-full")
            ui.checkbox("数据无变化 (Same) 时依然强制推送", value=bool(update.get("send_when_same", False))).bind_value(update, "send_when_same")

        with ui.card().classes("w-full p-6 bg-white no-shadow border border-gray-200 rounded-lg"):
            ui.label("数据未更新重试机制 (Wait)").classes("text-lg font-bold text-gray-700 mb-4")
            wait = config.setdefault("wait_for_change", {})
            ui.checkbox("启用 Same 状态重试等待", value=bool(wait.get("enabled", False))).bind_value(wait, "enabled").classes("mb-2")
            ui.number("轮询间隔 (秒)", value=wait.get("poll_interval_seconds", 300), min=1).bind_value(wait, "poll_interval_seconds").props("outlined bg-color=white dense mb-4").classes("w-full")
            ui.number("最大等待时长 (分钟)", value=wait.get("max_wait_minutes", 180), min=1).bind_value(wait, "max_wait_minutes").props("outlined bg-color=white dense").classes("w-full")

        with ui.card().classes("w-full p-6 bg-white no-shadow border border-gray-200 rounded-lg"):
            ui.label("Prefect 定时部署").classes("text-lg font-bold text-gray-700 mb-4")
            deployment = config.setdefault("deployment", {})
            ui.checkbox("随配置保存自动发布部署", value=bool(deployment.get("enabled", False))).bind_value(deployment, "enabled").classes("mb-2")
            ui.input("Cron 表达式 (例如 0 8 * * *)").bind_value(deployment, "cron").props("outlined bg-color=white dense mb-4").classes("w-full")
            ui.input("Cron 时区").bind_value(deployment, "timezone").props("outlined bg-color=white dense").classes("w-full")

        with ui.card().classes("w-full p-6 bg-white no-shadow border border-gray-200 rounded-lg"):
            ui.label("底层 JSON 实时预览").classes("text-lg font-bold text-gray-700 mb-4")
            preview = ui.textarea(value=json_text(config_to_save_payload(config))).classes("w-full font-mono text-xs").props("outlined bg-color=white readonly rows=10")
            ui.button("刷新 JSON", icon="refresh", on_click=lambda: preview.set_value(json_text(config_to_save_payload(config)))).props("flat color=primary size=sm mt-2")

@ui.page("/")
def index() -> None:
    ui.query('body').style('background-color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;')
    
    render_header()
    config = state["config"]
    
    with ui.column().classes("w-full max-w-7xl mx-auto mt-6 px-4 pb-12"):
        # Custom styled tabs
        with ui.card().classes("w-full p-0 no-shadow border border-gray-200 rounded-xl overflow-hidden bg-white mb-6"):
            tabs = ui.tabs().classes("w-full text-gray-600").props("active-color=primary indicator-color=primary align=justify narrow-indicator dense")
            with tabs:
                base_tab = ui.tab("基础信息", icon="info")
                download_tab = ui.tab("数据抓取", icon="cloud_download")
                compare_tab = ui.tab("报表比对", icon="compare_arrows")
                send_tab = ui.tab("图文推送", icon="send")
                advanced_tab = ui.tab("高级策略", icon="settings")
                
        panels = ui.tab_panels(tabs, value=base_tab).classes("w-full bg-transparent")
        with panels:
            with ui.tab_panel(base_tab).classes("p-0"):
                with ui.card().classes("w-full p-8 no-shadow border border-gray-200 rounded-xl bg-white"):
                    render_base_panel(config)
            with ui.tab_panel(download_tab).classes("p-0"):
                with ui.card().classes("w-full p-8 no-shadow border border-gray-200 rounded-xl bg-white"):
                    render_download_cards(config.setdefault("downloads", []), lambda: ui.navigate.reload())
            with ui.tab_panel(compare_tab).classes("p-0"):
                with ui.card().classes("w-full p-8 no-shadow border border-gray-200 rounded-xl bg-white"):
                    render_compare_panel(config)
            with ui.tab_panel(send_tab).classes("p-0"):
                with ui.card().classes("w-full p-8 no-shadow border border-gray-200 rounded-xl bg-white"):
                    render_send_panel(config)
            with ui.tab_panel(advanced_tab).classes("p-0"):
                render_advanced_panel(config)

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="通报配置后台", port=8081, reload=True, dark=False)
