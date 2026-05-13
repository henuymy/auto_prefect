"""Download configuration cards and dialogs for NiceGUI."""

from __future__ import annotations
import copy
from typing import Callable
from nicegui import ui
from .models import (
    AUTH_PRESETS, BODY_TYPE_OPTIONS, DEFAULT_EXCEL_COLUMNS, DOWNLOAD_MODE_LABELS,
    METHOD_OPTIONS, STAGE_OPTIONS, apply_response_mode_defaults, empty_download,
    json_text, parse_json_text, response_mode_label, response_mode_value,
)
from .request_ui import request_parser_panel

def _text_to_list(value: str) -> list[str]:
    return [part.strip() for part in (value or "").replace("\n", ",").split(",") if part.strip()]

def _summary(value: str, size: int = 72) -> str:
    value = value or ""
    return value if len(value) <= size else f"{value[:size]}..."

def section_title(text: str, icon: str) -> None:
    with ui.row().classes("items-center gap-2 mb-6 w-full pb-2 border-b border-gray-100"):
        ui.icon(icon, color="primary", size="sm")
        ui.label(text).classes("text-xl font-bold text-gray-800")

def render_download_cards(downloads: list[dict], on_change: Callable[[], None]) -> None:
    with ui.row().classes("items-center justify-between w-full mb-4"):
        section_title("数据源抓取配置", "cloud_download")
        ui.button("添加抓取项", icon="add", on_click=lambda: add_download(downloads, on_change)).props("outline color=primary size=sm")

    if not downloads:
        downloads.append(empty_download())

    for index, item in enumerate(downloads):
        render_download_card(downloads, index, item, on_change)

def add_download(downloads: list[dict], on_change: Callable[[], None]) -> None:
    downloads.append(empty_download())
    on_change()

def render_download_card(downloads: list[dict], index: int, item: dict, on_change: Callable[[], None]) -> None:
    with ui.card().classes("w-full p-5 mb-4 bg-white no-shadow border border-gray-200 rounded-lg hover:shadow-md transition-shadow"):
        with ui.row().classes("items-start justify-between w-full gap-4"):
            with ui.column().classes("gap-1 flex-1"):
                with ui.row().classes("items-center gap-3"):
                    ui.label(item.get("name") or f"未命名接口 {index + 1}").classes("text-lg font-bold text-gray-800")
                    ui.chip(item.get('method', 'POST'), color="indigo-1", text_color="indigo-8").props("size=sm square")
                    ui.chip(response_mode_label(item.get("response_mode", "file")), color="teal-1", text_color="teal-8", icon="file_download").props("size=sm square")
                
                ui.label(_summary(item.get('url', ''))).classes("text-gray-500 font-mono text-sm mt-2 break-all")
                
                body_type = item.get("body_type", "json")
                if body_type == "raw" and item.get("raw_body"):
                    ui.label(f"Payload: {_summary(item.get('raw_body'), 80)}").classes("text-gray-400 font-mono text-xs mt-1 break-all bg-gray-50 p-1 rounded")
                elif item.get("data"):
                    ui.label(f"Payload: {_summary(json_text(item.get('data')), 80)}").classes("text-gray-400 font-mono text-xs mt-1 break-all bg-gray-50 p-1 rounded")
                
                with ui.row().classes("items-center gap-4 mt-3 text-xs text-gray-500"):
                    with ui.row().classes("items-center gap-1"):
                        ui.icon("cookie", size="xs")
                        ui.label(f"Stage: {item.get('stage', '无')}")
                    with ui.row().classes("items-center gap-1"):
                        ui.icon("vpn_key", size="xs")
                        ui.label(f"认证: {item.get('auth_preset', '无')}")

            with ui.row().classes("gap-2 items-center"):
                ui.button(icon="edit", on_click=lambda i=index: open_download_dialog(downloads, i, on_change)).props("flat color=primary round")
                ui.button(icon="content_copy", on_click=lambda i=index: copy_download(downloads, i, on_change)).props("flat color=gray-500 round")
                ui.button(icon="delete", on_click=lambda i=index: delete_download(downloads, i, on_change)).props("flat color=negative round")

def copy_download(downloads: list[dict], index: int, on_change: Callable[[], None]) -> None:
    copied = copy.deepcopy(downloads[index])
    copied["name"] = f"{copied.get('name') or '下载'}_复制"
    downloads.insert(index + 1, copied)
    on_change()

def delete_download(downloads: list[dict], index: int, on_change: Callable[[], None]) -> None:
    if len(downloads) <= 1:
        ui.notify("至少保留一个下载项", type="warning")
        return
    downloads.pop(index)
    on_change()

def open_download_dialog(downloads: list[dict], index: int, on_change: Callable[[], None]) -> None:
    original = downloads[index]
    draft = copy.deepcopy(original)
    apply_response_mode_defaults(draft)

    dialog = ui.dialog().props("maximized transition-show=slide-up transition-hide=slide-down")
    with dialog, ui.card().classes("w-full h-full p-0 flex flex-col bg-gray-50 no-shadow"):
        with ui.row().classes("items-center justify-between w-full bg-white px-6 py-4 border-b border-gray-200 shadow-sm"):
            with ui.row().classes("items-center gap-3"):
                ui.icon("api", size="md", color="primary")
                ui.label(f"编辑数据源：{draft.get('name') or index + 1}").classes("text-xl font-bold text-gray-800")
            ui.button(icon="close", on_click=dialog.close).props("flat round color=gray-600")

        tabs = ui.tabs().classes("w-full text-gray-600 bg-white border-b border-gray-200").props("active-color=primary indicator-color=primary align=left dense")
        with tabs:
            basic_tab = ui.tab("基础请求", icon="http")
            parse_tab = ui.tab("CURL解析", icon="auto_fix_high")
            auth_tab = ui.tab("动态认证", icon="vpn_key")
            response_tab = ui.tab("响应提取", icon="transform")
            preview_tab = ui.tab("底层JSON", icon="code")

        panels = ui.tab_panels(tabs, value=basic_tab).classes("w-full flex-1 bg-transparent p-6 overflow-auto")
        with panels:
            with ui.tab_panel(basic_tab).classes("p-0 max-w-5xl mx-auto w-full"):
                with ui.card().classes("w-full p-8 bg-white rounded-xl border border-gray-200 no-shadow"):
                    render_basic_panel(draft)
            with ui.tab_panel(parse_tab).classes("p-0 max-w-5xl mx-auto w-full"):
                with ui.card().classes("w-full p-8 bg-white rounded-xl border border-gray-200 no-shadow"):
                    request_parser_panel(draft, lambda parsed: apply_parsed_request(draft, parsed))
            with ui.tab_panel(auth_tab).classes("p-0 max-w-5xl mx-auto w-full"):
                with ui.card().classes("w-full p-8 bg-white rounded-xl border border-gray-200 no-shadow"):
                    render_auth_panel(draft)
            with ui.tab_panel(response_tab).classes("p-0 max-w-5xl mx-auto w-full"):
                with ui.card().classes("w-full p-8 bg-white rounded-xl border border-gray-200 no-shadow"):
                    render_response_panel(draft)
            with ui.tab_panel(preview_tab).classes("p-0 max-w-5xl mx-auto w-full"):
                with ui.card().classes("w-full p-8 bg-white rounded-xl border border-gray-200 no-shadow"):
                    preview = ui.code(json_text(draft), language="json").classes("w-full shadow-sm rounded border border-gray-200")
                    ui.button("刷新", icon="refresh", on_click=lambda: preview.set_content(json_text(draft))).props("outline mt-4")

        with ui.row().classes("justify-end items-center w-full px-6 py-4 bg-white border-t border-gray-200 mt-auto"):
            ui.button("取消", on_click=dialog.close).props("flat color=gray-600").classes("mr-2")
            ui.button("保存配置", icon="check", on_click=lambda: save_download_dialog(downloads, index, draft, dialog, on_change)).props("color=primary unelevated")
    dialog.open()

def render_basic_panel(draft: dict) -> None:
    ui.label("核心参数").classes("text-lg font-bold text-gray-700 mb-4")
    with ui.grid(columns=2).classes("w-full gap-6 mb-6"):
        ui.input("标识名称").bind_value(draft, "name").props("outlined bg-color=white")
        ui.select(STAGE_OPTIONS, label="Cookie 组 (Stage)").bind_value(draft, "stage").props("outlined bg-color=white")
        ui.select(METHOD_OPTIONS, label="HTTP 方法").bind_value(draft, "method").props("outlined bg-color=white")
        ui.select(BODY_TYPE_OPTIONS, label="Payload 格式").bind_value(draft, "body_type").props("outlined bg-color=white")
    ui.input("接口完整 URL").bind_value(draft, "url").classes("w-full mb-8").props("outlined bg-color=white")

    ui.separator().classes("mb-8")
    ui.label("请求体明细").classes("text-lg font-bold text-gray-700 mb-4")

    headers_text = {"value": json_text(draft.get("headers") or {})}
    data_text = {"value": json_text(draft.get("data") or {})}
    raw_text = {"value": draft.get("raw_body") or ""}

    with ui.grid(columns=2).classes("w-full gap-6"):
        with ui.column().classes("w-full"):
            ui.label("Headers (JSON)").classes("text-sm font-bold text-gray-500 mb-1")
            headers_input = ui.textarea(value=headers_text["value"]).classes("w-full").props("outlined bg-color=white font-mono text-xs rows=4")
        with ui.column().classes("w-full"):
            ui.label("Data/JSON Payload (JSON)").classes("text-sm font-bold text-gray-500 mb-1")
            data_input = ui.textarea(value=data_text["value"]).classes("w-full").props("outlined bg-color=white font-mono text-xs rows=4")
    
    with ui.column().classes("w-full mt-6"):
        ui.label("Raw Body (Text)").classes("text-sm font-bold text-gray-500 mb-1")
        raw_input = ui.textarea(value=raw_text["value"]).classes("w-full").props("outlined bg-color=white font-mono text-xs rows=4")

    def apply_text() -> None:
        try:
            draft["headers"] = parse_json_text(headers_input.value, {})
            draft["data"] = parse_json_text(data_input.value, {})
            draft["raw_body"] = raw_input.value or ""
            ui.notify("已暂时保存到内存，点击底部保存生效", type="positive")
        except Exception as exc:
            ui.notify(f"JSON 格式错误: {exc}", type="negative")

    with ui.row().classes("w-full justify-end mt-4"):
        ui.button("应用文本框修改", on_click=apply_text).props("outline color=primary")

def apply_parsed_request(draft: dict, parsed: dict) -> None:
    for key, value in parsed.items():
        if key in {"method", "url", "headers", "body_type", "data", "raw_body", "auth_preset"}:
            draft[key] = value
    for key in (
        "headers_from_cookies", "csrf_headers_from_cookies", "headers_from_session_storage",
        "headers_from_local_storage", "headers_from_cookie_string",
    ):
        if key in parsed:
            draft[key] = parsed[key]
    ui.notify("解析内容已回填，点击底部保存生效", type="positive")

def render_auth_panel(draft: dict) -> None:
    ui.label("动态认证配置").classes("text-lg font-bold text-gray-700 mb-2")
    ui.label("拦截并转换 Cookie 或 Storage 中的凭证，作为请求 Header 发送。").classes("text-gray-500 mb-6 text-sm")
    
    preset_select = ui.select(AUTH_PRESETS, label="快捷预设模板").bind_value(draft, "auth_preset").classes("w-full max-w-md mb-8").props("outlined bg-color=white")
    
    fields = [
        ("Cookie 取值 -> Header (JSON)", "headers_from_cookies"),
        ("动态 CSRF Header (JSON)", "csrf_headers_from_cookies"),
        ("SessionStorage 取值 -> Header (JSON)", "headers_from_session_storage"),
        ("LocalStorage 取值 -> Header (JSON)", "headers_from_local_storage"),
        ("Cookie串转 -> Header (JSON)", "headers_from_cookie_string"),
    ]
    textareas = {}
    with ui.grid(columns=2).classes("w-full gap-6"):
        for label, key in fields:
            with ui.column().classes("w-full"):
                ui.label(label).classes("text-sm font-bold text-gray-500 mb-1")
                textareas[key] = ui.textarea(value=json_text(draft.get(key) or {})).classes("w-full").props("outlined bg-color=white font-mono text-xs")

    def apply_auth() -> None:
        try:
            for _, key in fields:
                draft[key] = parse_json_text(textareas[key].value, {})
            ui.notify("动态认证已应用到当前编辑项", type="positive")
        except Exception as exc:
            ui.notify(f"JSON 格式错误: {exc}", type="negative")

    def refresh_ui() -> None:
        preset_select.set_value(draft.get("auth_preset"))
        for _, key in fields:
            textareas[key].set_value(json_text(draft.get(key) or {}))

    with ui.row().classes("gap-3 mt-6 justify-end w-full"):
        ui.button("套用 SSR 默认", on_click=lambda: (apply_auth_preset(draft, "SSR Cookie"), refresh_ui())).props("flat color=primary")
        ui.button("套用智慧运营", on_click=lambda: (apply_auth_preset(draft, "智慧运营 User-Info"), refresh_ui())).props("flat color=primary")
        ui.button("套用地市作战", on_click=lambda: (apply_city_ops_auth(draft), refresh_ui())).props("flat color=primary")
        ui.button("应用修改", on_click=apply_auth).props("outline color=primary")

def apply_auth_preset(draft: dict, preset: str) -> None:
    draft["auth_preset"] = preset
    if preset == "SSR Cookie":
        draft["headers_from_cookies"] = {"ssr-token": "ssr-token"}
        draft["csrf_headers_from_cookies"] = {"ssr-header": "ssr-token"}
    elif preset == "智慧运营 User-Info":
        draft["headers_from_session_storage"] = {"User-Info": "zhyyptInfo.accessToken"}
    ui.notify(f"已套用 {preset} 模板", type="positive")

def apply_city_ops_auth(draft: dict) -> None:
    draft["auth_preset"] = "自定义高级"
    draft["headers_from_cookie_string"] = {"uapToken": "*"}
    ui.notify("已套用地市作战模板", type="positive")

def render_response_panel(draft: dict) -> None:
    ui.label("响应数据处理").classes("text-lg font-bold text-gray-700 mb-4")
    
    mode_label = response_mode_label(draft.get("response_mode", "file"))
    mode_select = ui.select(list(DOWNLOAD_MODE_LABELS.values()), value=mode_label, label="提取模式").classes("w-full max-w-md mb-6").props("outlined bg-color=white")

    excel_area = ui.column().classes("w-full gap-4 bg-gray-50 p-6 rounded-lg border border-gray-200")

    def render_mode_fields() -> None:
        draft["response_mode"] = response_mode_value(mode_select.value)
        apply_response_mode_defaults(draft)
        excel_area.clear()
        if draft["response_mode"] == "file":
            with excel_area:
                ui.icon("file_download_done", size="md", color="gray-400")
                ui.label("接口直接返回二进制 Excel 或 Zip，系统将直接保存为文件。").classes("text-gray-600")
            return
            
        with excel_area:
            ui.label("JSON 转换为 Excel 规则").classes("font-bold text-gray-700 mb-2")
            excel = draft.setdefault("excel", {})
            with ui.grid(columns=2).classes("w-full gap-6 mb-4"):
                ui.input("定位数据列表路径 (用.分割)", value=excel.get("data_path", "result.tableData")).bind_value(excel, "data_path").props("outlined bg-color=white")
                ui.input("导出至内部 Sheet 名", value=excel.get("sheet_name", "明细数据")).bind_value(excel, "sheet_name").props("outlined bg-color=white")
            
            ui.label("Excel 列定义 (JSON)").classes("text-sm font-bold text-gray-500 mb-1")
            columns_input = ui.textarea(value=json_text(excel.get("columns") or DEFAULT_EXCEL_COLUMNS)).classes("w-full").props("outlined bg-color=white font-mono text-xs")
            
            if draft["response_mode"] == "json_drilldown_to_excel":
                ui.separator().classes("my-4")
                ui.label("级联下钻参数").classes("font-bold text-gray-700 mb-2")
                drilldown = draft.setdefault("drilldown", {})
                with ui.grid(columns=2).classes("w-full gap-6"):
                    ui.input("下钻根层级 ID (逗号分隔)", value=",".join(drilldown.get("levels") or [])).bind_value(drilldown, "levels_text").props("outlined bg-color=white")
                    ui.input("请求参数 Area 字段名", value=drilldown.get("request_area_field", "areaId")).bind_value(drilldown, "request_area_field").props("outlined bg-color=white")
                    ui.input("响应中下一层 Area 字段名", value=drilldown.get("next_area_field", "areaCode")).bind_value(drilldown, "next_area_field").props("outlined bg-color=white")
                    ui.number("最大请求次数上限", value=drilldown.get("max_requests", 1000), min=1).bind_value(drilldown, "max_requests").props("outlined bg-color=white")

            def apply_response() -> None:
                try:
                    excel["columns"] = parse_json_text(columns_input.value, [])
                    if draft["response_mode"] == "json_drilldown_to_excel":
                        drilldown = draft.setdefault("drilldown", {})
                        drilldown["data_path"] = excel.get("data_path", "result.tableData")
                        drilldown["levels"] = _text_to_list(drilldown.pop("levels_text", ""))
                    ui.notify("响应处理参数已更新", type="positive")
                except Exception as exc:
                    ui.notify(f"JSON 格式错误: {exc}", type="negative")

            with ui.row().classes("w-full justify-end mt-4"):
                ui.button("应用提取规则", on_click=apply_response).props("outline color=primary")

    mode_select.on("update:model-value", lambda _: render_mode_fields())
    render_mode_fields()

def save_download_dialog(downloads: list[dict], index: int, draft: dict, dialog, on_change: Callable[[], None]) -> None:
    apply_response_mode_defaults(draft)
    downloads[index] = copy.deepcopy(draft)
    dialog.close()
    on_change()
    ui.notify("下载项更新成功", type="positive")