"""Download configuration cards and dialogs for NiceGUI."""

from __future__ import annotations

import copy
from typing import Callable

from nicegui import ui

from .models import (
    AUTH_PRESETS,
    BODY_TYPE_OPTIONS,
    DEFAULT_EXCEL_COLUMNS,
    DOWNLOAD_MODE_LABELS,
    METHOD_OPTIONS,
    STAGE_OPTIONS,
    apply_response_mode_defaults,
    empty_download,
    json_text,
    parse_json_text,
    response_mode_label,
    response_mode_value,
)
from .request_ui import request_parser_panel


def _text_to_list(value: str) -> list[str]:
    return [part.strip() for part in (value or "").replace("\n", ",").split(",") if part.strip()]


def _summary(value: str, size: int = 72) -> str:
    value = value or ""
    return value if len(value) <= size else f"{value[:size]}..."


def render_download_cards(downloads: list[dict], on_change: Callable[[], None]) -> None:
    with ui.row().classes("items-center justify-between w-full"):
        ui.label("下载配置").classes("text-xl font-bold")
        ui.button("新增下载项", icon="add", on_click=lambda: add_download(downloads, on_change)).props("color=primary")

    if not downloads:
        downloads.append(empty_download())

    for index, item in enumerate(downloads):
        render_download_card(downloads, index, item, on_change)


def add_download(downloads: list[dict], on_change: Callable[[], None]) -> None:
    downloads.append(empty_download())
    on_change()


def render_download_card(downloads: list[dict], index: int, item: dict, on_change: Callable[[], None]) -> None:
    with ui.card().classes("w-full download-card"):
        with ui.row().classes("items-start justify-between w-full gap-4"):
            with ui.column().classes("gap-1"):
                ui.label(item.get("name") or f"未命名下载 {index + 1}").classes("text-lg font-bold")
                ui.label(response_mode_label(item.get("response_mode", "file"))).classes("text-blue-7")
                ui.label(f"{item.get('method', 'POST')} {_summary(item.get('url', ''))}").classes("text-grey-7")
                ui.label(f"Cookie Stage: {item.get('stage', '')} | 动态认证: {item.get('auth_preset', '无')}").classes(
                    "text-grey-7 text-sm"
                )
            with ui.row().classes("gap-2"):
                ui.button("编辑", icon="edit", on_click=lambda i=index: open_download_dialog(downloads, i, on_change)).props(
                    "outline"
                )
                ui.button("复制", icon="content_copy", on_click=lambda i=index: copy_download(downloads, i, on_change)).props(
                    "outline"
                )
                ui.button("删除", icon="delete", on_click=lambda i=index: delete_download(downloads, i, on_change)).props(
                    "outline color=negative"
                )


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

    dialog = ui.dialog().props("maximized")
    with dialog, ui.card().classes("w-full h-full nice-dialog-card"):
        with ui.row().classes("items-center justify-between w-full"):
            ui.label(f"编辑下载项：{draft.get('name') or index + 1}").classes("text-2xl font-bold")
            ui.button(icon="close", on_click=dialog.close).props("flat round")

        tabs = ui.tabs().classes("w-full")
        with tabs:
            basic_tab = ui.tab("基础请求")
            parse_tab = ui.tab("请求识别")
            auth_tab = ui.tab("动态认证")
            response_tab = ui.tab("响应处理")
            preview_tab = ui.tab("JSON 预览")

        panels = ui.tab_panels(tabs, value=basic_tab).classes("w-full flex-1")
        with panels:
            with ui.tab_panel(basic_tab):
                render_basic_panel(draft)
            with ui.tab_panel(parse_tab):
                request_parser_panel(draft, lambda parsed: apply_parsed_request(draft, parsed))
            with ui.tab_panel(auth_tab):
                render_auth_panel(draft)
            with ui.tab_panel(response_tab):
                render_response_panel(draft)
            with ui.tab_panel(preview_tab):
                preview = ui.code(json_text(draft), language="json").classes("w-full")
                ui.button("刷新预览", on_click=lambda: preview.set_content(json_text(draft))).props("outline")

        with ui.row().classes("justify-end w-full gap-3"):
            ui.button("取消", on_click=dialog.close).props("outline")
            ui.button("保存下载项", on_click=lambda: save_download_dialog(downloads, index, draft, dialog, on_change)).props(
                "color=primary"
            )
    dialog.open()


def render_basic_panel(draft: dict) -> None:
    with ui.grid(columns=2).classes("w-full gap-4"):
        ui.input("下载标识").bind_value(draft, "name")
        ui.select(STAGE_OPTIONS, label="Cookie Stage").bind_value(draft, "stage")
        ui.select(METHOD_OPTIONS, label="请求方法").bind_value(draft, "method")
        ui.select(BODY_TYPE_OPTIONS, label="载体类型").bind_value(draft, "body_type")
    ui.input("下载 URL").bind_value(draft, "url").classes("w-full")

    headers_text = {"value": json_text(draft.get("headers") or {})}
    data_text = {"value": json_text(draft.get("data") or {})}
    raw_text = {"value": draft.get("raw_body") or ""}

    ui.label("固定请求头 JSON").classes("font-bold")
    headers_input = ui.textarea(value=headers_text["value"]).classes("w-full")
    ui.label("请求体 data/json").classes("font-bold")
    data_input = ui.textarea(value=data_text["value"]).classes("w-full")
    ui.label("raw body").classes("font-bold")
    raw_input = ui.textarea(value=raw_text["value"]).classes("w-full")

    def apply_text() -> None:
        try:
            draft["headers"] = parse_json_text(headers_input.value, {})
            draft["data"] = parse_json_text(data_input.value, {})
            draft["raw_body"] = raw_input.value or ""
            ui.notify("基础请求已应用到当前编辑项", type="positive")
        except Exception as exc:
            ui.notify(f"JSON 格式错误: {exc}", type="negative")

    ui.button("应用基础请求修改", on_click=apply_text).props("outline")


def apply_parsed_request(draft: dict, parsed: dict) -> None:
    for key, value in parsed.items():
        if key in {"method", "url", "headers", "body_type", "data", "raw_body", "auth_preset"}:
            draft[key] = value
    for key in (
        "headers_from_cookies",
        "csrf_headers_from_cookies",
        "headers_from_session_storage",
        "headers_from_local_storage",
        "headers_from_cookie_string",
    ):
        if key in parsed:
            draft[key] = parsed[key]
    ui.notify("已回填到当前下载项，保存弹窗后生效", type="positive")


def render_auth_panel(draft: dict) -> None:
    ui.select(AUTH_PRESETS, label="动态认证预设").bind_value(draft, "auth_preset").classes("w-full")
    ui.label("常用：SSR Cookie 用 ssr-token；智慧运营 User-Info 用 sessionStorage；地市作战 uapToken 可用 Cookie 串转 Header。").classes(
        "text-grey-7"
    )
    fields = [
        ("Cookie 转 Header JSON", "headers_from_cookies"),
        ("动态 CSRF JSON", "csrf_headers_from_cookies"),
        ("sessionStorage 转 Header JSON", "headers_from_session_storage"),
        ("localStorage 转 Header JSON", "headers_from_local_storage"),
        ("Cookie串转 Header JSON", "headers_from_cookie_string"),
    ]
    textareas = {}
    for label, key in fields:
        ui.label(label).classes("font-bold")
        textareas[key] = ui.textarea(value=json_text(draft.get(key) or {})).classes("w-full")

    def apply_auth() -> None:
        try:
            for _, key in fields:
                draft[key] = parse_json_text(textareas[key].value, {})
            ui.notify("动态认证已应用到当前编辑项", type="positive")
        except Exception as exc:
            ui.notify(f"JSON 格式错误: {exc}", type="negative")

    with ui.row().classes("gap-2"):
        ui.button("套用 SSR 默认认证", on_click=lambda: apply_auth_preset(draft, "SSR Cookie")).props("outline")
        ui.button("套用智慧运营 User-Info", on_click=lambda: apply_auth_preset(draft, "智慧运营 User-Info")).props("outline")
        ui.button("套用地市作战 uapToken", on_click=lambda: apply_city_ops_auth(draft)).props("outline")
        ui.button("应用动态认证修改", on_click=apply_auth).props("color=primary")


def apply_auth_preset(draft: dict, preset: str) -> None:
    draft["auth_preset"] = preset
    if preset == "SSR Cookie":
        draft["headers_from_cookies"] = {"ssr-token": "ssr-token"}
        draft["csrf_headers_from_cookies"] = {"ssr-header": "ssr-token"}
    elif preset == "智慧运营 User-Info":
        draft["headers_from_session_storage"] = {"User-Info": "zhyyptInfo.accessToken"}
    ui.notify(f"已套用 {preset}，保存弹窗后生效", type="positive")


def apply_city_ops_auth(draft: dict) -> None:
    draft["auth_preset"] = "自定义高级"
    draft["headers_from_cookie_string"] = {"uapToken": "*"}
    ui.notify("已套用地市作战 uapToken，保存弹窗后生效", type="positive")


def render_response_panel(draft: dict) -> None:
    mode_label = response_mode_label(draft.get("response_mode", "file"))
    mode_select = ui.select(list(DOWNLOAD_MODE_LABELS.values()), value=mode_label, label="响应处理方式").classes("w-full")

    excel_area = ui.column().classes("w-full gap-3")

    def render_mode_fields() -> None:
        draft["response_mode"] = response_mode_value(mode_select.value)
        apply_response_mode_defaults(draft)
        excel_area.clear()
        if draft["response_mode"] == "file":
            with excel_area:
                ui.label("接口直接返回 Excel 或文件时使用此模式。").classes("text-grey-7")
            return
        with excel_area:
            excel = draft.setdefault("excel", {})
            ui.input("JSON 数据路径", value=excel.get("data_path", "result.tableData")).bind_value(excel, "data_path")
            ui.input("Excel Sheet", value=excel.get("sheet_name", "地市作战明细")).bind_value(excel, "sheet_name")
            columns_input = ui.textarea("Excel 列配置 JSON", value=json_text(excel.get("columns") or DEFAULT_EXCEL_COLUMNS)).classes(
                "w-full"
            )
            if draft["response_mode"] == "json_drilldown_to_excel":
                drilldown = draft.setdefault("drilldown", {})
                ui.input("下钻层级（逗号分隔）", value=",".join(drilldown.get("levels") or [])).bind_value(
                    drilldown, "levels_text"
                )
                ui.input("请求 area 字段", value=drilldown.get("request_area_field", "areaId")).bind_value(
                    drilldown, "request_area_field"
                )
                ui.input("下一层字段", value=drilldown.get("next_area_field", "areaCode")).bind_value(
                    drilldown, "next_area_field"
                )
                ui.number("最大请求数", value=drilldown.get("max_requests", 1000), min=1).bind_value(
                    drilldown, "max_requests"
                )

            def apply_response() -> None:
                try:
                    excel["columns"] = parse_json_text(columns_input.value, [])
                    if draft["response_mode"] == "json_drilldown_to_excel":
                        drilldown = draft.setdefault("drilldown", {})
                        drilldown["data_path"] = excel.get("data_path", "result.tableData")
                        drilldown["levels"] = _text_to_list(drilldown.pop("levels_text", ""))
                    ui.notify("响应处理已应用到当前编辑项", type="positive")
                except Exception as exc:
                    ui.notify(f"JSON 格式错误: {exc}", type="negative")

            ui.button("应用响应处理修改", on_click=apply_response).props("color=primary")

    mode_select.on("update:model-value", lambda _: render_mode_fields())
    render_mode_fields()


def save_download_dialog(downloads: list[dict], index: int, draft: dict, dialog, on_change: Callable[[], None]) -> None:
    apply_response_mode_defaults(draft)
    downloads[index] = copy.deepcopy(draft)
    dialog.close()
    on_change()
    ui.notify("下载项已保存", type="positive")

