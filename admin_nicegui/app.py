"""NiceGUI report configuration admin.

Run with:
    python -m admin_nicegui.app
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import nicegui.run as nicegui_run
from nicegui import ui

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from .config_store import (  # noqa: E402
    PROJECT_DIR,
    list_report_names,
    list_templates,
    load_report,
    load_stage_titles,
    save_draft,
    save_report,
    save_task_config,
)
from .deploy import deploy_report  # noqa: E402
from .download_ui import render_download_cards  # noqa: E402
from .models import empty_config, empty_send_item, json_text, normalize_config, parse_json_text  # noqa: E402
from .serializers import config_to_save_payload  # noqa: E402


state = {
    "selected": "",
    "config": normalize_config(empty_config()),
    "dirty_token": 0,
}


_original_nicegui_setup = nicegui_run.setup


def _safe_nicegui_setup() -> None:
    """Avoid startup failure in restricted Windows environments.

    NiceGUI initializes a ProcessPoolExecutor on startup.  Some locked-down
    Windows desktops deny creating the multiprocessing pipe.  The admin UI does
    not use NiceGUI's background process helpers, so it is safe to continue
    without that optional process pool.
    """
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
            ui.notify(f"草稿已保存：{path.name}", type="positive")
            return
        if deploy:
            task_path = save_task_config(payload)
            deployment = payload.get("deployment") or {}
            ok, output = deploy_report(
                PROJECT_DIR,
                payload["name"],
                task_path,
                cron=deployment.get("cron", "") if deployment.get("enabled") else "",
                timezone=deployment.get("timezone", "Asia/Shanghai"),
            )
            if ok:
                ui.notify("配置已保存并部署成功", type="positive")
            else:
                ui.notify("部署失败，请查看输出", type="negative")
                show_text_dialog("部署输出", output)
        else:
            ui.notify(f"配置已保存：{path.name}", type="positive")
        state["selected"] = payload["name"]
    except Exception as exc:
        ui.notify(f"保存失败: {exc}", type="negative")


def show_text_dialog(title: str, content: str) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-3/4"):
        ui.label(title).classes("text-xl font-bold")
        ui.textarea(value=content).props("readonly").classes("w-full")
        ui.button("关闭", on_click=dialog.close)
    dialog.open()


def render_sidebar() -> None:
    with ui.left_drawer(value=True).classes("bg-blue-grey-1"):
        ui.label("通报配置").classes("text-2xl font-bold q-mb-md")
        ui.button("新建报表", icon="add", on_click=new_report).classes("w-full").props("color=primary")
        ui.separator()
        for name in list_report_names():
            props = "flat no-caps align=left"
            if name == state.get("selected"):
                props += " color=primary"
            ui.button(name, on_click=lambda n=name: select_report(n)).classes("w-full justify-start").props(props)


def render_base_panel(config: dict) -> None:
    ui.label("基础信息").classes("section-title")
    ui.input("通报名称").bind_value(config, "name").classes("w-full")
    templates = list_templates()
    selected_template = config.get("template_path") or (templates[0] if templates else "")
    if templates:
        ui.select(templates, value=selected_template, label="模板文件").bind_value(config, "template_path").classes("w-full")
    else:
        ui.input("模板路径").bind_value(config, "template_path").classes("w-full")


def render_compare_panel(config: dict) -> None:
    ui.label("比对配置").classes("section-title")
    compare_sources = config.setdefault("compare_sources", [])
    download_names = [item.get("name") for item in config.get("downloads", []) if item.get("name")]

    def add_compare() -> None:
        compare_sources.append(
            {
                "download_name": download_names[0] if download_names else "",
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
        )
        ui.navigate.reload()

    ui.button("新增比对来源", icon="add", on_click=add_compare).props("outline")
    for source_index, source in enumerate(compare_sources):
        with ui.card().classes("w-full"):
            ui.select(download_names or [source.get("download_name", "")], label="对应下载标识").bind_value(
                source, "download_name"
            ).classes("w-full")
            mappings = source.setdefault("sheet_mappings", [])
            if not mappings:
                mappings.append({})
            for mapping in mappings:
                with ui.grid(columns=3).classes("w-full gap-3"):
                    ui.input("映射备注").bind_value(mapping, "name")
                    ui.input("源 sheet").bind_value(mapping, "new_sheet_name")
                    ui.input("模板 sheet").bind_value(mapping, "template_sheet_name")
                    ui.number("表头行", value=mapping.get("header_row", 1), min=1).bind_value(mapping, "header_row")
                    ui.input("忽略列（逗号分隔）", value=",".join(mapping.get("ignore_columns") or [])).on(
                        "blur",
                        lambda e, m=mapping: m.update(
                            {"ignore_columns": [x.strip() for x in (e.sender.value or "").split(",") if x.strip()]}
                        ),
                    )
                    ui.input("主键列（逗号分隔）", value=",".join(mapping.get("key_columns") or [])).on(
                        "blur",
                        lambda e, m=mapping: m.update(
                            {"key_columns": [x.strip() for x in (e.sender.value or "").split(",") if x.strip()]}
                        ),
                    )
            ui.button(
                "删除此比对来源",
                icon="delete",
                on_click=lambda i=source_index: (compare_sources.pop(i), ui.navigate.reload()),
            ).props("outline color=negative")


def render_send_panel(config: dict) -> None:
    ui.label("发送配置").classes("section-title")
    send = config.setdefault("send", {})
    send.setdefault("items", [empty_send_item()])
    ui.input("Workbook 名称").bind_value(send, "workbook_name").classes("w-full")
    ui.input("企业微信 Webhook URL").bind_value(send, "webhook_url").classes("w-full")
    ui.button("新增发送项", icon="add", on_click=lambda: (send["items"].append(empty_send_item()), ui.navigate.reload())).props(
        "outline"
    )
    for index, item in enumerate(send["items"]):
        with ui.card().classes("w-full"):
            with ui.grid(columns=4).classes("w-full gap-3"):
                ui.select(["image", "text"], label="类型").bind_value(item, "type")
                ui.input("sheet").bind_value(item, "sheet")
                text_cfg = item.setdefault("text", {})
                ui.select(["", "used_range"], value=text_cfg.get("mode", ""), label="文本模式").bind_value(text_cfg, "mode")
                ui.button(
                    "删除",
                    icon="delete",
                    on_click=lambda i=index: (send["items"].pop(i), ui.navigate.reload()),
                ).props("outline color=negative")


def render_advanced_panel(config: dict) -> None:
    with ui.expansion("高级：模板更新、等待重试、定时部署、JSON 预览", icon="settings").classes("w-full"):
        update = config.setdefault("template_update", {})
        with ui.grid(columns=3).classes("w-full gap-3"):
            ui.select(["any_changed", "all_changed"], label="更新条件").bind_value(update, "update_condition")
            ui.select(["changed", "all_compared"], label="写入范围").bind_value(update, "write_sheets")
            ui.checkbox("same 也发送", value=bool(update.get("send_when_same", False))).bind_value(update, "send_when_same")

        wait = config.setdefault("wait_for_change", {})
        with ui.grid(columns=3).classes("w-full gap-3"):
            ui.checkbox("启用 same 等待重试", value=bool(wait.get("enabled", False))).bind_value(wait, "enabled")
            ui.number("重试间隔秒", value=wait.get("poll_interval_seconds", 300), min=1).bind_value(
                wait, "poll_interval_seconds"
            )
            ui.number("最大等待分钟", value=wait.get("max_wait_minutes", 180), min=1).bind_value(wait, "max_wait_minutes")

        deployment = config.setdefault("deployment", {})
        with ui.grid(columns=3).classes("w-full gap-3"):
            ui.checkbox("启用定时部署", value=bool(deployment.get("enabled", False))).bind_value(deployment, "enabled")
            ui.input("Cron").bind_value(deployment, "cron")
            ui.input("时区").bind_value(deployment, "timezone")

        preview = ui.textarea(value=json_text(config_to_save_payload(config))).classes("w-full")
        ui.button("刷新 JSON 预览", on_click=lambda: preview.set_value(json_text(config_to_save_payload(config)))).props("outline")


def render_stage_titles() -> None:
    stage_titles = load_stage_titles()
    if not stage_titles:
        return
    with ui.expansion("Cookie Stage 说明", icon="cookie").classes("w-full"):
        for stage, title in stage_titles.items():
            ui.label(f"{stage}: {title}")


@ui.page("/")
def index() -> None:
    ui.add_head_html(
        """
        <style>
        body { background: #f5f7fb; }
        .nice-page { max-width: 1380px; margin: 0 auto; }
        .section-title { font-size: 20px; font-weight: 800; margin-top: 18px; }
        .download-card { border: 1px solid #e4e8f0; box-shadow: 0 10px 24px rgba(31, 45, 61, 0.06); }
        .nice-dialog-card { overflow: auto; }
        </style>
        """
    )
    render_sidebar()
    config = state["config"]
    with ui.column().classes("nice-page w-full gap-4 q-pa-xl"):
        with ui.row().classes("items-center justify-between w-full"):
            ui.label("NiceGUI 通报配置后台").classes("text-3xl font-bold")
            with ui.row().classes("gap-2"):
                ui.button("保存草稿", on_click=lambda: save_current(draft=True)).props("outline")
                ui.button("保存配置", on_click=lambda: save_current()).props("color=primary")
                ui.button("保存并部署", on_click=lambda: save_current(deploy=True)).props("color=secondary")
        render_base_panel(config)
        render_stage_titles()
        render_download_cards(config.setdefault("downloads", []), lambda: ui.navigate.reload())
        render_compare_panel(config)
        render_send_panel(config)
        render_advanced_panel(config)


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="通报配置后台", port=8080, reload=False)
