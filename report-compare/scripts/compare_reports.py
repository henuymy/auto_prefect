"""Compare downloaded report sheets with corresponding template sheets."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG_PATH = SKILL_DIR / "config.json"


@dataclass
class CompareResult:
    result: str
    diff_summary: dict[str, Any] = field(default_factory=dict)
    new_row_count: int = 0
    old_row_count: int = 0
    message: str = ""


@dataclass
class WorkbookCompareResult:
    result: str
    sheets: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    message: str = ""


def require_win32():
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise RuntimeError("缺少 pywin32，无法通过 Excel COM 读取 .xls/.xlsx 文件") from exc
    return win32com.client


def load_json(path):
    resolved = Path(path).resolve()
    with resolved.open("r", encoding="utf-8") as f:
        return json.load(f), resolved


def resolve_path(base_dir, value):
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def normalize_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(Decimal(str(value)).normalize())
    if isinstance(value, int):
        return str(value)
    text = str(value).replace("\u3000", " ").strip()
    if text == "":
        return ""
    try:
        decimal_value = Decimal(text.replace(",", ""))
    except (InvalidOperation, ValueError):
        return " ".join(text.split())
    if decimal_value == decimal_value.to_integral():
        return str(decimal_value.to_integral())
    return str(decimal_value.normalize())


def normalize_matrix(values):
    return [[normalize_cell(cell) for cell in row] for row in values]


def is_empty_row(row):
    return all(cell == "" for cell in row)


def trim_empty_edges(rows):
    rows = [list(row) for row in rows if not is_empty_row(row)]
    if not rows:
        return []

    max_width = max(len(row) for row in rows)
    padded = [row + [""] * (max_width - len(row)) for row in rows]
    non_empty_cols = [
        index
        for index in range(max_width)
        if any(row[index] != "" for row in padded)
    ]
    if not non_empty_cols:
        return []
    return [row[non_empty_cols[0]:non_empty_cols[-1] + 1] for row in padded]


def values_to_matrix(values):
    if values is None:
        return []
    if not isinstance(values, tuple):
        return [[values]]
    if values and not isinstance(values[0], tuple):
        return [list(values)]
    return [list(row) for row in values]


def workbook_sheet_names(workbook):
    return [workbook.Worksheets(i).Name for i in range(1, workbook.Worksheets.Count + 1)]


def get_sheet(workbook, sheet_name):
    try:
        return workbook.Worksheets(sheet_name)
    except Exception as exc:
        names = workbook_sheet_names(workbook)
        raise KeyError(f"找不到工作表 {sheet_name!r}，当前工作表: {names}") from exc


def read_sheet_table(workbook, sheet_name, range_address=None):
    sheet = get_sheet(workbook, sheet_name)
    rng = sheet.Range(range_address) if range_address else sheet.UsedRange
    return trim_empty_edges(normalize_matrix(values_to_matrix(rng.Value)))


def split_header_rows(rows, header_row=1):
    if header_row < 1:
        raise ValueError("header_row 必须从 1 开始")
    if not rows:
        return [], []
    header_index = header_row - 1
    if header_index >= len(rows):
        raise ValueError(f"header_row={header_row} 超出数据行数 {len(rows)}")
    return rows[header_index], [row for row in rows[header_index + 1:] if not is_empty_row(row)]


def unique_headers(headers):
    result = []
    seen = {}
    for index, header in enumerate(headers, start=1):
        name = header or f"列{index}"
        count = seen.get(name, 0)
        seen[name] = count + 1
        if count:
            name = f"{name}_{count + 1}"
        result.append(name)
    return result


def filter_columns(headers, rows, ignore_columns=None):
    ignored = set(ignore_columns or [])
    indexes = [index for index, header in enumerate(headers) if header not in ignored]
    return (
        [headers[index] for index in indexes],
        [[row[index] if index < len(row) else "" for index in indexes] for row in rows],
    )


def rows_to_dicts(headers, rows):
    return [
        {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}
        for row in rows
    ]


def row_key(row, key_columns):
    return tuple(row.get(column, "") for column in key_columns)


def compare_without_key(new_rows, old_rows, sample_limit):
    changed_rows = []
    max_rows = max(len(new_rows), len(old_rows))
    changed_count = 0
    for index in range(max_rows):
        new_row = new_rows[index] if index < len(new_rows) else None
        old_row = old_rows[index] if index < len(old_rows) else None
        if new_row != old_row:
            changed_count += 1
            if len(changed_rows) < sample_limit:
                changed_rows.append({"row_number": index + 1, "old": old_row, "new": new_row})
    return {
        "added_rows": max(len(new_rows) - len(old_rows), 0),
        "removed_rows": max(len(old_rows) - len(new_rows), 0),
        "changed_rows": changed_count,
        "sample_diffs": changed_rows,
    }


def compare_with_key(headers, new_rows, old_rows, key_columns, sample_limit):
    missing_keys = [column for column in key_columns if column not in headers]
    if missing_keys:
        return {"invalid_reason": f"主键字段不存在: {missing_keys}"}

    new_items = rows_to_dicts(headers, new_rows)
    old_items = rows_to_dicts(headers, old_rows)
    new_map = {row_key(row, key_columns): row for row in new_items}
    old_map = {row_key(row, key_columns): row for row in old_items}
    if len(new_map) != len(new_items) or len(old_map) != len(old_items):
        return {"invalid_reason": "主键字段存在重复值，无法按主键比对"}

    added_keys = sorted(set(new_map) - set(old_map))
    removed_keys = sorted(set(old_map) - set(new_map))
    changed = []
    for key in sorted(set(new_map) & set(old_map)):
        changed_columns = [
            header for header in headers
            if new_map[key].get(header, "") != old_map[key].get(header, "")
        ]
        if changed_columns:
            changed.append({
                "key": key,
                "changed_columns": changed_columns,
                "old": {column: old_map[key].get(column, "") for column in changed_columns},
                "new": {column: new_map[key].get(column, "") for column in changed_columns},
            })
    return {
        "added_rows": len(added_keys),
        "removed_rows": len(removed_keys),
        "changed_rows": len(changed),
        "sample_added_keys": added_keys[:sample_limit],
        "sample_removed_keys": removed_keys[:sample_limit],
        "sample_diffs": changed[:sample_limit],
    }


def compare_tables(new_table, old_table, header_row=1, ignore_columns=None, key_columns=None, sample_limit=10):
    new_rows = trim_empty_edges(normalize_matrix(new_table))
    old_rows = trim_empty_edges(normalize_matrix(old_table))
    if not new_rows:
        return CompareResult("invalid", message="新报表数据为空")
    if not old_rows:
        return CompareResult("invalid", new_row_count=len(new_rows), message="模板旧数据为空")

    new_headers, new_data = split_header_rows(new_rows, header_row=header_row)
    old_headers, old_data = split_header_rows(old_rows, header_row=header_row)
    new_headers = unique_headers(new_headers)
    old_headers = unique_headers(old_headers)
    new_headers, new_data = filter_columns(new_headers, new_data, ignore_columns)
    old_headers, old_data = filter_columns(old_headers, old_data, ignore_columns)

    if new_headers != old_headers:
        return CompareResult(
            "invalid",
            diff_summary={
                "new_headers": new_headers,
                "old_headers": old_headers,
                "missing_in_new": [header for header in old_headers if header not in new_headers],
                "missing_in_old": [header for header in new_headers if header not in old_headers],
            },
            new_row_count=len(new_data),
            old_row_count=len(old_data),
            message="新报表和模板旧数据字段不一致",
        )

    if key_columns:
        diff = compare_with_key(new_headers, new_data, old_data, key_columns, sample_limit)
        if diff.get("invalid_reason"):
            return CompareResult(
                "invalid",
                diff_summary=diff,
                new_row_count=len(new_data),
                old_row_count=len(old_data),
                message=diff["invalid_reason"],
            )
    else:
        diff = compare_without_key(new_data, old_data, sample_limit)

    changed = any(diff.get(name, 0) for name in ("added_rows", "removed_rows", "changed_rows"))
    return CompareResult(
        "changed" if changed else "same",
        diff_summary=diff,
        new_row_count=len(new_data),
        old_row_count=len(old_data),
        message="数据有变化" if changed else "数据一致",
    )


def normalize_sheet_mapping(item):
    if isinstance(item, str):
        return {"name": item, "new_sheet_name": item, "template_sheet_name": item}
    if not isinstance(item, dict):
        raise ValueError(f"sheet_mappings 只支持字符串或对象: {item!r}")
    new_sheet_name = item.get("new_sheet_name") or item.get("new_sheet") or item.get("sheet")
    template_sheet_name = item.get("template_sheet_name") or item.get("template_sheet") or new_sheet_name
    if not new_sheet_name:
        raise ValueError(f"sheet_mappings 缺少 new_sheet_name: {item!r}")
    return {
        "name": item.get("name") or new_sheet_name,
        "new_sheet_name": new_sheet_name,
        "template_sheet_name": template_sheet_name,
        "new_range": item.get("new_range"),
        "template_range": item.get("template_range"),
        "header_row": item.get("header_row"),
        "ignore_columns": item.get("ignore_columns"),
        "key_columns": item.get("key_columns"),
    }


def build_sheet_mappings(config, new_workbook, template_workbook):
    if config.get("sheet_mappings"):
        return [normalize_sheet_mapping(item) for item in config["sheet_mappings"]]

    if config.get("new_sheet_name") or config.get("template_sheet_name"):
        new_sheet_name = config.get("new_sheet_name")
        template_sheet_name = config.get("template_sheet_name") or new_sheet_name
        if not new_sheet_name:
            raise ValueError("配置了 template_sheet_name 时，也必须配置 new_sheet_name")
        return [{
            "name": new_sheet_name,
            "new_sheet_name": new_sheet_name,
            "template_sheet_name": template_sheet_name,
            "new_range": config.get("new_range"),
            "template_range": config.get("template_range"),
        }]

    skip_sheets = set(config.get("skip_sheets") or [])
    template_sheet_names = set(workbook_sheet_names(template_workbook))
    mappings = []
    for sheet_name in workbook_sheet_names(new_workbook):
        if sheet_name in skip_sheets:
            continue
        mappings.append({
            "name": sheet_name,
            "new_sheet_name": sheet_name,
            "template_sheet_name": sheet_name,
            "missing_template_sheet": sheet_name not in template_sheet_names,
        })
    return mappings


def merge_sheet_config(config, mapping):
    return {
        "header_row": int(mapping.get("header_row") or config.get("header_row", 1)),
        "ignore_columns": mapping.get("ignore_columns")
        if mapping.get("ignore_columns") is not None
        else config.get("ignore_columns") or [],
        "key_columns": mapping.get("key_columns")
        if mapping.get("key_columns") is not None
        else config.get("key_columns") or [],
        "sample_limit": int(config.get("sample_limit", 10)),
    }


def compare_workbook(config):
    new_path = Path(config["new_report_path"]).resolve()
    template_path = Path(config["template_path"]).resolve()
    if not new_path.exists():
        raise FileNotFoundError(f"新报表不存在: {new_path}")
    if not template_path.exists():
        raise FileNotFoundError(f"模板文件不存在: {template_path}")

    win32com = require_win32()
    excel = win32com.DispatchEx("Excel.Application")
    excel.Visible = bool(config.get("visible", False))
    excel.DisplayAlerts = False
    excel.AskToUpdateLinks = False
    new_workbook = None
    template_workbook = None
    sheet_results = []
    try:
        new_workbook = excel.Workbooks.Open(
            str(new_path),
            UpdateLinks=0,
            ReadOnly=True,
            IgnoreReadOnlyRecommended=True,
        )
        template_workbook = excel.Workbooks.Open(
            str(template_path),
            UpdateLinks=0,
            ReadOnly=True,
            IgnoreReadOnlyRecommended=True,
        )
        mappings = build_sheet_mappings(config, new_workbook, template_workbook)
        template_sheet_names = set(workbook_sheet_names(template_workbook))
        for mapping in mappings:
            new_sheet_name = mapping["new_sheet_name"]
            template_sheet_name = mapping["template_sheet_name"]
            if template_sheet_name not in template_sheet_names:
                sheet_results.append({
                    "name": mapping.get("name") or new_sheet_name,
                    "new_sheet_name": new_sheet_name,
                    "template_sheet_name": template_sheet_name,
                    "result": "invalid",
                    "message": f"模板缺少对应工作表: {template_sheet_name}",
                })
                continue

            options = merge_sheet_config(config, mapping)
            result = compare_tables(
                read_sheet_table(new_workbook, new_sheet_name, range_address=mapping.get("new_range")),
                read_sheet_table(template_workbook, template_sheet_name, range_address=mapping.get("template_range")),
                header_row=options["header_row"],
                ignore_columns=options["ignore_columns"],
                key_columns=options["key_columns"],
                sample_limit=options["sample_limit"],
            )
            payload = asdict(result)
            payload.update({
                "name": mapping.get("name") or new_sheet_name,
                "new_sheet_name": new_sheet_name,
                "template_sheet_name": template_sheet_name,
            })
            sheet_results.append(payload)
    finally:
        if new_workbook is not None:
            new_workbook.Close(SaveChanges=False)
        if template_workbook is not None:
            template_workbook.Close(SaveChanges=False)
        excel.Quit()

    summary = {
        "same": sum(1 for item in sheet_results if item.get("result") == "same"),
        "changed": sum(1 for item in sheet_results if item.get("result") == "changed"),
        "invalid": sum(1 for item in sheet_results if item.get("result") == "invalid"),
        "total": len(sheet_results),
    }
    if summary["invalid"]:
        result = "invalid"
        message = "存在无法比对的工作表"
    elif summary["changed"]:
        result = "changed"
        message = "至少一个工作表数据有变化"
    else:
        result = "same"
        message = "所有工作表数据一致"
    return WorkbookCompareResult(result=result, sheets=sheet_results, summary=summary, message=message)


def result_to_dict(result):
    return asdict(result)


def write_result(output_path, result):
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(result_to_dict(result), f, ensure_ascii=False, indent=2)
    return path


def config_from_args(args):
    config_arg = args.config
    if not config_arg and DEFAULT_CONFIG_PATH.exists():
        config_arg = DEFAULT_CONFIG_PATH

    if config_arg:
        config, config_path = load_json(config_arg)
        base_dir = config_path.parent
    else:
        config = {}
        base_dir = SKILL_DIR

    cli_values = {
        "new_report_path": args.new_report,
        "template_path": args.template,
        "new_sheet_name": args.new_sheet,
        "template_sheet_name": args.template_sheet,
        "new_range": args.new_range,
        "template_range": args.template_range,
        "header_row": args.header_row,
        "sample_limit": args.sample_limit,
        "visible": args.visible,
    }
    for key, value in cli_values.items():
        if value is not None:
            config[key] = value
    if args.ignore_column:
        config["ignore_columns"] = args.ignore_column
    if args.key_column:
        config["key_columns"] = args.key_column
    if args.skip_sheet:
        config["skip_sheets"] = args.skip_sheet
    if args.output:
        config["output_path"] = args.output

    for key in ("new_report_path", "template_path", "output_path"):
        if config.get(key):
            config[key] = str(resolve_path(base_dir, config[key]))
    return config


def validate_config(config):
    required = ["new_report_path", "template_path"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"缺少必要配置: {missing}")


def run(config):
    validate_config(config)
    result = compare_workbook(config)
    output_path = config.get("output_path")
    if output_path:
        written_path = write_result(output_path, result)
        print(f"[INFO] 比对结果已写入: {written_path}")
    print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description="对比新下载报表和模板中的对应工作表。")
    parser.add_argument("--config", default=None, help=f"配置文件路径，默认可使用 {DEFAULT_CONFIG_PATH}")
    parser.add_argument("--new-report", default=None, help="新下载报表路径")
    parser.add_argument("--template", default=None, help="正式模板路径")
    parser.add_argument("--new-sheet", default=None, help="只比对指定的新报表工作表；默认比对新报表所有工作表")
    parser.add_argument("--template-sheet", default=None, help="指定模板对应工作表；不传则按同名工作表匹配")
    parser.add_argument("--new-range", default=None, help="新报表对比区域，例如 A1:K100；默认 UsedRange")
    parser.add_argument("--template-range", default=None, help="模板对比区域，例如 A1:K100；默认 UsedRange")
    parser.add_argument("--header-row", type=int, default=None, help="表头所在行，从对比区域第 1 行开始计数")
    parser.add_argument("--ignore-column", action="append", default=[], help="忽略字段，可重复传入")
    parser.add_argument("--key-column", action="append", default=[], help="主键字段，可重复传入；不传则按行顺序比对")
    parser.add_argument("--skip-sheet", action="append", default=[], help="整本比对时跳过指定工作表，可重复传入")
    parser.add_argument("--sample-limit", type=int, default=None, help="差异样例最大数量")
    parser.add_argument("--visible", action="store_true", help="读取时显示 Excel")
    parser.add_argument("--output", default=None, help="结果 JSON 输出路径")
    args = parser.parse_args()
    run(config_from_args(args))


if __name__ == "__main__":
    main()
