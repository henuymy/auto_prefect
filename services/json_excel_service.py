"""Helpers for converting JSON API responses to Excel files."""

from __future__ import annotations

import copy
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from openpyxl import Workbook


NUMERIC_TEXT_PATTERN = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


def get_by_path(payload, path: str):
    current = payload
    for part in [item.strip() for item in str(path or "").split(".") if item.strip()]:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def validate_success_response(payload):
    if isinstance(payload, dict) and payload.get("reCode") not in (None, "0000"):
        re_code = str(payload.get("reCode") or "")
        re_msg = str(payload.get("reMsg") or "")
        if re_code in {"1101", "401", "403"} or "登录" in re_msg or "超时" in re_msg:
            raise RuntimeError(
                f"JSON 接口返回 session 已过期: reCode={payload.get('reCode')}, reMsg={payload.get('reMsg')}"
            )
        raise RuntimeError(
            f"JSON 接口返回失败: reCode={payload.get('reCode')}, reMsg={payload.get('reMsg')}"
        )


def normalize_columns(excel_config: dict):
    columns = excel_config.get("columns") or []
    normalized = []
    for column in columns:
        field = (column.get("field") or "").strip()
        header = (column.get("header") or field).strip()
        if field:
            normalized.append({
                "field": field,
                "header": header,
                "type": str(column.get("type") or "").strip().lower(),
            })
    if not normalized:
        raise ValueError("excel.columns 不能为空")
    return normalized


def coerce_excel_value(value, column: dict):
    if column.get("type") != "number":
        return value
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return value

    text = value.strip().replace(",", "")
    if not text:
        return None
    if not NUMERIC_TEXT_PATTERN.match(text):
        return value
    number = float(text)
    return int(number) if number.is_integer() else number


def extract_rows(payload, data_path: str):
    validate_success_response(payload)
    rows = get_by_path(payload, data_path)
    if rows is None:
        raise RuntimeError(f"JSON 响应中找不到数据路径: {data_path}")
    if not isinstance(rows, list):
        raise RuntimeError(f"JSON 数据路径不是数组: {data_path}")
    return rows


def write_rows_to_excel(rows: list[dict], output_path, excel_config: dict):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    columns = normalize_columns(excel_config)
    sheet_name = excel_config.get("sheet_name") or "Sheet1"

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = str(sheet_name)[:31] or "Sheet1"
    worksheet.append([column["header"] for column in columns])
    for row in rows:
        worksheet.append([
            coerce_excel_value(get_by_path(row, column["field"]), column)
            for column in columns
        ])
    workbook.save(output_path)
    return {
        "rows": len(rows),
        "sheet_name": worksheet.title,
        "output_path": str(output_path),
    }


def json_response_to_excel(response_json, output_path, excel_config: dict):
    data_path = excel_config.get("data_path") or "result.tableData"
    rows = extract_rows(response_json, data_path)
    return write_rows_to_excel(rows, output_path, excel_config)


def set_by_path(payload: dict, path: str, value):
    parts = [item.strip() for item in str(path or "").split(".") if item.strip()]
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


def drilldown_json_to_excel(
    initial_payload: dict,
    fetch_next: Callable[[dict], dict],
    output_path,
    drilldown_config: dict,
    excel_config: dict,
    initial_response_json=None,
):
    data_path = drilldown_config.get("data_path") or "result.tableData"
    next_area_field = drilldown_config.get("next_area_field") or "areaCode"
    request_area_field = drilldown_config.get("request_area_field") or "areaId"
    levels = drilldown_config.get("levels") or ["区县", "网格", "渠道/门店", "人员"]
    max_requests = int(drilldown_config.get("max_requests") or 1000)
    max_workers = int(drilldown_config.get("max_workers") or drilldown_config.get("concurrency") or 6)
    max_workers = max(1, min(max_workers, 32))
    skip_self_row = bool(drilldown_config.get("skip_self_row", False))

    initial_area_id = get_by_path(initial_payload, request_area_field)
    queue = deque([
        {
            "payload": copy.deepcopy(initial_payload),
            "level_index": 0,
            "parent_area_id": "",
            "request_area_id": initial_area_id,
            "response": initial_response_json,
        }
    ])
    requested = set()
    if initial_area_id:
        requested.add(str(initial_area_id))

    all_rows = []
    request_count = 0
    while queue:
        current_level = []
        first_level_index = int(queue[0]["level_index"])
        while queue and int(queue[0]["level_index"]) == first_level_index:
            current_level.append(queue.popleft())

        fetch_items = [item for item in current_level if item.get("response") is None]
        if request_count + len(fetch_items) > max_requests:
            raise RuntimeError(f"级联下钻超过最大请求数: {max_requests}")
        if fetch_items:
            if max_workers == 1 or len(fetch_items) == 1:
                fetched_responses = [fetch_next(item["payload"]) for item in fetch_items]
            else:
                with ThreadPoolExecutor(max_workers=min(max_workers, len(fetch_items))) as executor:
                    fetched_responses = list(executor.map(lambda item: fetch_next(item["payload"]), fetch_items))
            for item, response_json in zip(fetch_items, fetched_responses):
                item["response"] = response_json
            request_count += len(fetch_items)

        for item in current_level:
            level_index = int(item["level_index"])
            level_name = levels[level_index] if level_index < len(levels) else f"层级{level_index + 1}"
            rows = extract_rows(item.get("response"), data_path)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                enriched = {
                    **row,
                    "__level_index": level_index,
                    "__level_name": level_name,
                    "__parent_area_id": item.get("parent_area_id") or "",
                    "__request_area_id": item.get("request_area_id") or "",
                }

                next_area_id = row.get(next_area_field)
                if skip_self_row and str(next_area_id or "") == str(item.get("request_area_id") or ""):
                    continue
                all_rows.append(enriched)
                if level_index >= len(levels) - 1 or not next_area_id:
                    continue
                next_area_id = str(next_area_id)
                if next_area_id in requested:
                    continue
                requested.add(next_area_id)
                next_payload = copy.deepcopy(item["payload"])
                set_by_path(next_payload, request_area_field, next_area_id)
                queue.append({
                    "payload": next_payload,
                    "level_index": level_index + 1,
                    "parent_area_id": item.get("request_area_id") or "",
                    "request_area_id": next_area_id,
                    "response": None,
                })

    write_result = write_rows_to_excel(all_rows, output_path, excel_config)
    return {
        **write_result,
        "requests": request_count,
        "max_workers": max_workers,
        "levels": levels,
    }
