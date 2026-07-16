"""Read Tencent Docs Smartbook subtables and save them as local workbooks."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from urllib.parse import quote

import requests
from openpyxl import Workbook

from services.tencent_sheet_service import (
    DOCS_BASE_URL,
    PROJECT_DIR,
    TencentDocsApiError,
    TencentDocsCredentials,
    auth_headers,
    is_retryable_api_error,
    load_credentials,
    load_tencent_docs_config,
    parse_api_json,
    request_api_json,
    resolve_file_id,
    resolve_path,
    retry_settings,
    safe_filename,
    safe_sheet_name,
)


def request_post_api_json(
    session: requests.Session,
    url: str,
    credentials: TencentDocsCredentials,
    timeout: int,
    body: dict[str, Any],
    retry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Request a Tencent Docs POST endpoint with the same retry policy as Sheets."""
    retry = retry or {}
    attempts = max(1, int(retry.get("attempts") or 1))
    backoff = max(0, float(retry.get("backoff_seconds") or 0))
    max_backoff = max(backoff, float(retry.get("max_backoff_seconds") or backoff))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.post(url, headers=auth_headers(credentials), json=body, timeout=timeout)
            return parse_api_json(response)
        except requests.RequestException as exc:
            last_error = exc
            retryable = True
        except TencentDocsApiError as exc:
            last_error = exc
            retryable = is_retryable_api_error(exc)
        if attempt >= attempts or not retryable:
            raise last_error
        sleep(min(max_backoff, backoff * (2 ** (attempt - 1))))
    raise RuntimeError("腾讯文档接口请求失败")


def response_data(payload: dict[str, Any]) -> dict[str, Any] | list[Any]:
    data = payload.get("data")
    return data if isinstance(data, (dict, list)) else payload


def response_items(data: dict[str, Any] | list[Any], *keys: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    for key in keys:
        values = data.get(key)
        if isinstance(values, list):
            return [item for item in values if isinstance(item, dict)]
    return []


def response_has_more(data: dict[str, Any] | list[Any], count: int, offset: int, limit: int) -> bool:
    if not isinstance(data, dict):
        return count >= limit
    for key in ("hasMore", "has_more", "more"):
        if key in data:
            return bool(data[key])
    for key in ("total", "totalCount", "total_count"):
        try:
            return offset + count < int(data[key])
        except (TypeError, ValueError):
            continue
    return count >= limit


def response_next_offset(data: dict[str, Any] | list[Any], offset: int, count: int) -> int:
    if isinstance(data, dict):
        for key in ("next", "nextOffset", "next_offset"):
            if key not in data:
                continue
            try:
                return int(data[key])
            except (TypeError, ValueError) as exc:
                raise RuntimeError("腾讯智能表格分页响应包含无效 next 偏移量") from exc
    return offset + count


def smart_value_to_text(value: Any) -> Any:
    """Turn Smartbook's rich field values into readable spreadsheet text."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return ", ".join(str(item) for item in (smart_value_to_text(item) for item in value) if item not in (None, ""))
    if isinstance(value, dict):
        if isinstance(value.get("options"), list) and "value" in value:
            selected = value["value"]
            selected_values = selected if isinstance(selected, list) else [selected]
            option_labels = {
                str(option.get("id")): option.get("text") or option.get("name") or option.get("title")
                for option in value["options"]
                if isinstance(option, dict) and option.get("id") is not None
            }
            return ", ".join(
                str(item)
                for item in (smart_value_to_text(option_labels.get(str(selected_value), selected_value)) for selected_value in selected_values)
                if item not in (None, "")
            )
        for key in ("text", "name", "title", "label", "url"):
            text = value.get(key)
            if text not in (None, ""):
                return smart_value_to_text(text)
        if "value" in value:
            return smart_value_to_text(value["value"])
        for key in ("link", "data", "items"):
            if value.get(key) not in (None, ""):
                return smart_value_to_text(value[key])
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def subtable_id(item: dict[str, Any]) -> str:
    return str(item.get("sheetID") or item.get("sheetId") or item.get("sheet_id") or item.get("id") or "").strip()


def subtable_name(item: dict[str, Any]) -> str:
    return str(item.get("title") or item.get("name") or item.get("sheetName") or "").strip()


def field_id(item: dict[str, Any]) -> str:
    return str(item.get("fieldID") or item.get("fieldId") or item.get("field_id") or item.get("id") or item.get("fieldTitle") or "").strip()


def field_name(item: dict[str, Any]) -> str:
    return str(item.get("fieldTitle") or item.get("title") or item.get("name") or item.get("fieldName") or field_id(item)).strip()


def record_value(values: dict[str, Any], field: dict[str, Any]) -> Any:
    for key in (field_id(field), field_name(field)):
        if key in values:
            return values[key]
    return None


class SmartbookClient:
    def __init__(self, config: dict[str, Any], report: dict[str, Any], session: requests.Session | None = None):
        self.config = config
        self.credentials = load_credentials(config)
        self.session = session or requests.Session()
        self.timeout = int(report.get("request_timeout_seconds") or config.get("request_timeout_seconds") or 30)
        self.retry = retry_settings(config, report)
        limits = config.get("smartbook_read_limits") or {}
        self.page_size = max(1, int(report.get("smartbook_page_size") or limits.get("page_size") or 100))

    def resolve_file_id(self, report: dict[str, Any]) -> str:
        return resolve_file_id(self.session, report, self.credentials, self.timeout, retry=self.retry)

    def list_sheets(self, file_id: str) -> list[dict[str, Any]]:
        payload = request_api_json(
            self.session,
            f"{DOCS_BASE_URL}/openapi/smartbook/v2/files/{quote(file_id, safe='$')}/sheets",
            self.credentials,
            self.timeout,
            retry=self.retry,
        )
        return response_items(response_data(payload), "getSheet", "sheets", "items", "list")

    def _list_paged(self, file_id: str, sheet_id: str, request_name: str, response_name: str) -> list[dict[str, Any]]:
        url = (
            f"{DOCS_BASE_URL}/openapi/smartbook/v2/files/{quote(file_id, safe='$')}/sheets/"
            f"{quote(sheet_id, safe='')}"
        )
        items: list[dict[str, Any]] = []
        offset = 0
        seen_offsets: set[int] = set()
        while True:
            if offset in seen_offsets:
                raise RuntimeError(f"腾讯智能表格{response_name}分页重复偏移量: {offset}")
            seen_offsets.add(offset)
            payload = request_post_api_json(
                self.session,
                url,
                self.credentials,
                self.timeout,
                {request_name: {"offset": offset, "limit": self.page_size}},
                retry=self.retry,
            )
            data = response_data(payload)
            operation_data = data.get(request_name) if isinstance(data, dict) and isinstance(data.get(request_name), (dict, list)) else data
            page_items = response_items(operation_data, response_name, "items", "list")
            items.extend(page_items)
            has_more = response_has_more(operation_data, len(page_items), offset, self.page_size)
            if not page_items:
                if has_more:
                    raise RuntimeError(f"腾讯智能表格{response_name}分页返回空数据但仍标记 hasMore")
                return items
            if not has_more:
                return items
            next_offset = response_next_offset(operation_data, offset, len(page_items))
            if next_offset <= offset:
                raise RuntimeError(f"腾讯智能表格{response_name}分页偏移量未前进: {next_offset}")
            offset = next_offset

    def list_fields(self, file_id: str, sheet_id: str) -> list[dict[str, Any]]:
        return self._list_paged(file_id, sheet_id, "getFields", "fields")

    def list_records(self, file_id: str, sheet_id: str) -> list[dict[str, Any]]:
        return self._list_paged(file_id, sheet_id, "getRecords", "records")


def select_subtables(available: list[dict[str, Any]], configured: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not configured:
        raise ValueError("腾讯智能表格抓取至少需要一个子表")
    by_id = {subtable_id(item): item for item in available if subtable_id(item)}
    by_name = {subtable_name(item): item for item in available if subtable_name(item)}
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    used_ids: set[str] = set()
    for selector in configured:
        if "range" in selector:
            raise ValueError("智能表格子表不支持 range；请使用 sheet_id 或 sheet_name 选择子表")
        configured_id = str(selector.get("sheet_id") or "").strip()
        configured_name = str(selector.get("sheet_name") or "").strip()
        if not configured_id and not configured_name:
            raise ValueError("智能表格子表必须填写 sheet_id 或 sheet_name")
        subtable = by_id.get(configured_id) if configured_id else by_name.get(configured_name)
        if not subtable:
            target = configured_id or configured_name
            raise RuntimeError(f"腾讯智能表格中找不到子表: {target}")
        resolved_id = subtable_id(subtable)
        if not resolved_id:
            raise RuntimeError("腾讯智能表格子表响应缺少 sheetID")
        if resolved_id in used_ids:
            raise ValueError(f"智能表格子表不能重复: {resolved_id}")
        used_ids.add(resolved_id)
        selected.append((subtable, selector))
    return selected


def write_smartbook_workbook(
    client: SmartbookClient,
    file_id: str,
    report: dict[str, Any],
    output_dir: str | Path,
    base_dir: Path = PROJECT_DIR,
) -> dict[str, Any]:
    started = perf_counter()
    selected = select_subtables(client.list_sheets(file_id), list(report.get("sheets") or []))
    workbook = Workbook()
    default_sheet = workbook.active
    used_names: set[str] = set()
    sheet_results: list[dict[str, Any]] = []
    try:
        for index, (subtable, selector) in enumerate(selected):
            sheet_id = subtable_id(subtable)
            fields = [item for item in client.list_fields(file_id, sheet_id) if field_id(item)]
            if not fields:
                raise RuntimeError(f"腾讯智能表格子表缺少字段: {sheet_id}")
            output_name = safe_sheet_name(
                selector.get("output_sheet_name") or subtable_name(subtable) or sheet_id,
                used_names,
            )
            worksheet = default_sheet if index == 0 else workbook.create_sheet()
            worksheet.title = output_name
            worksheet.append([field_name(item) for item in fields])
            records = client.list_records(file_id, sheet_id)
            exported_records = 0
            for record in records:
                values = record.get("values")
                if not isinstance(values, dict) or not values:
                    continue
                worksheet.append([smart_value_to_text(record_value(values, item)) for item in fields])
                exported_records += 1
            sheet_results.append(
                {
                    "sheet_id": sheet_id,
                    "output_sheet_name": output_name,
                    "fetched_records": len(records),
                    "records": exported_records,
                }
            )

        resolved_output_dir = resolve_path(output_dir, base_dir=base_dir)
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        filename = safe_filename(report.get("output_filename") or f"{report.get('name') or '腾讯智能表格'}.xlsx")
        if not filename.lower().endswith((".xlsx", ".xlsm")):
            filename = f"{filename}.xlsx"
        output_path = resolved_output_dir / filename
        try:
            workbook.save(output_path)
        except PermissionError as exc:
            raise PermissionError(f"腾讯智能表格下载结果无法保存，文件可能正被 Excel 打开，请关闭后重试: {output_path}") from exc
    finally:
        workbook.close()

    return {
        "name": report.get("name"),
        "source": "tencent_smartbook",
        "transport": "tencent_docs_openapi",
        "file_id": file_id,
        "bytes": output_path.stat().st_size,
        "output_path": str(output_path),
        "sheets": sheet_results,
        "downloaded_at": datetime.now().isoformat(),
        "timings": {"total_seconds": round(perf_counter() - started, 3)},
    }


def download_tencent_smartbook_report(
    report: dict[str, Any],
    output_dir: str | Path,
    base_dir: Path = PROJECT_DIR,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Download configured Smartbook subtables into a single XLSX workbook."""
    config = load_tencent_docs_config(report.get("tencent_config_path"), base_dir=base_dir)
    client = SmartbookClient(config, report, session=session)
    file_id = client.resolve_file_id(report)
    return write_smartbook_workbook(client, file_id, report, output_dir, base_dir=base_dir)
