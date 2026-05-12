"""Request parsing UI helpers for NiceGUI."""

from __future__ import annotations

from typing import Callable

from nicegui import ui

from utils.request_parser import headers_from_header_rows, parse_request_by_mode


PARSE_MODES = {
    "推荐：cURL（bash）": "curl",
    "分开填写 URL + 请求头 + 请求体": "headers_body",
    "完整 HTTP 请求 / Request Headers": "raw_http",
    "PowerShell（实验）": "powershell",
    "fetch（实验）": "fetch",
    "HAR（实验）": "har",
}


def _default_enabled_headers(header_rows: list[dict]) -> list[dict]:
    runtime_prefixes = ("sec-", "sec-fetch-")
    blocked = {"cookie", "content-length", "host", "connection", "accept-encoding"}
    rows = []
    for row in header_rows:
        copied = dict(row)
        name = (copied.get("name") or "").lower()
        copied["enabled"] = not (name in blocked or any(name.startswith(prefix) for prefix in runtime_prefixes))
        rows.append(copied)
    return rows


def request_parser_panel(target: dict, on_apply: Callable[[dict], None]) -> None:
    state = {
        "mode_label": "推荐：cURL（bash）",
        "raw_request": "",
        "method": target.get("method", "POST"),
        "url": target.get("url", ""),
        "headers_text": "",
        "body": "",
        "parsed": None,
        "header_rows": [],
    }

    ui.label("推荐从浏览器 Network 里复制 Copy as cURL (bash)。识别后可勾选需要发送的请求头。").classes(
        "text-grey-7"
    )
    mode_select = ui.select(list(PARSE_MODES.keys()), value=state["mode_label"], label="解析方式").classes("w-full")
    raw_input = ui.textarea(
        label="复制内容",
        placeholder="粘贴 cURL bash / 完整 HTTP 请求 / fetch / PowerShell / HAR",
    ).classes("w-full")

    with ui.expansion("高级：分开填写 / 补充字段", icon="tune").classes("w-full"):
        with ui.row().classes("w-full gap-4"):
            method_input = ui.select(["POST", "GET", "PUT", "PATCH", "DELETE"], value=state["method"], label="请求方法").classes(
                "w-40"
            )
            url_input = ui.input("请求 URL", value=state["url"]).classes("flex-1")
        headers_input = ui.textarea(
            label="请求头原文",
            placeholder="Accept: */*\nContent-Type: application/json\nUser-Info: ...",
        ).classes("w-full")
        body_input = ui.textarea(label="请求体原文", placeholder='{"queryDate":"${today_yyyymmdd}"}').classes("w-full")

    result_area = ui.column().classes("w-full gap-3")

    def parse_request() -> None:
        try:
            parsed = parse_request_by_mode(
                PARSE_MODES[mode_select.value],
                raw_request=raw_input.value or "",
                method=method_input.value or "POST",
                url=url_input.value or "",
                headers_text=headers_input.value or "",
                body=body_input.value or "",
            )
            state["parsed"] = parsed
            state["header_rows"] = _default_enabled_headers(parsed.get("header_rows") or [])
            render_result()
            ui.notify("识别成功，请确认请求头后回填", type="positive")
        except Exception as exc:
            ui.notify(f"识别失败: {exc}", type="negative")

    def render_result() -> None:
        result_area.clear()
        parsed = state["parsed"]
        if not parsed:
            return
        with result_area:
            ui.separator()
            ui.label("识别结果确认").classes("text-lg font-bold")
            ui.label(f"方法：{parsed.get('method')}，载体类型：{parsed.get('body_type')}")
            ui.code(parsed.get("url", "")).classes("w-full")
            ui.label("默认已取消 Cookie、Content-Length、Host、Connection、Accept-Encoding、sec-* 等浏览器运行时头。").classes(
                "text-grey-7"
            )
            for row in state["header_rows"]:
                with ui.row().classes("items-center w-full gap-3"):
                    ui.checkbox(value=bool(row.get("enabled", True))).bind_value(row, "enabled")
                    ui.input("请求头字段").bind_value(row, "name").classes("w-48")
                    ui.input("值").bind_value(row, "value").classes("flex-1")
            if parsed.get("body_type") == "raw":
                ui.textarea("raw body", value=parsed.get("raw_body", "")).props("readonly").classes("w-full")
            else:
                import json

                ui.code(json.dumps(parsed.get("data", {}), ensure_ascii=False, indent=2), language="json").classes("w-full")
            ui.button("确认回填到当前下载项", on_click=apply_parsed).props("color=primary")

    def apply_parsed() -> None:
        parsed = state["parsed"]
        if not parsed:
            return
        selected_headers = headers_from_header_rows(state["header_rows"])
        result = {
            "method": parsed.get("method") or "POST",
            "url": parsed.get("url") or "",
            "headers": selected_headers,
            "body_type": parsed.get("body_type") or "form",
            "data": parsed.get("data") or {},
            "raw_body": parsed.get("raw_body") or "",
        }
        lower_headers = {key.lower(): key for key in selected_headers}
        if "user-info" in lower_headers or "userinfo" in lower_headers:
            result["headers"].pop(lower_headers.get("user-info") or lower_headers.get("userinfo"), None)
            result["auth_preset"] = "智慧运营 User-Info"
            result["headers_from_session_storage"] = {"User-Info": "zhyyptInfo.accessToken"}
        if "ssr-token" in lower_headers:
            result["headers"].pop(lower_headers["ssr-token"], None)
            result["auth_preset"] = "SSR Cookie"
            result["headers_from_cookies"] = {"ssr-token": "ssr-token"}
            result["csrf_headers_from_cookies"] = {"ssr-header": "ssr-token"}
        on_apply(result)

    ui.button("识别请求", on_click=parse_request).props("color=primary")
    render_result()

