"""Build image/text message packages from Excel workbooks."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path


from infrastructure.excel_client import require_win32, get_sheet, open_excel, open_workbook

PROJECT_DIR = Path(__file__).resolve().parents[1]
COPY_APPEARANCE = {
    "screen": 1,
    "printer": 2,
}
COPY_FORMAT = {
    "picture": -4147,
    "bitmap": 2,
}


def resolve_path(value, base_dir=PROJECT_DIR):
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path


def get_output_paths(config, base_dir=PROJECT_DIR):
    output_config = config.get("output", {})
    runtime_dir = resolve_path(output_config.get("runtime_dir", "runtime"), base_dir)
    image_dir = resolve_path(output_config.get("image_dir", "images"), runtime_dir)
    package_file = resolve_path(output_config.get("package_file", "message_package.json"), runtime_dir)
    preview_file = resolve_path(output_config.get("preview_image_file", "preview.png"), runtime_dir)
    return image_dir, package_file, preview_file


def merge_capture_config(defaults, item_config):
    merged = dict(defaults or {})
    merged.update(item_config or {})
    return merged


def normalize_2d(values):
    if values is None:
        return [[None]]
    if not isinstance(values, tuple):
        return [[values]]
    if values and not isinstance(values[0], tuple):
        return [list(values)]
    return [list(row) for row in values]


def has_value(value):
    return value is not None and str(value).strip() != ""


def used_range_bounds(ws, shrink_empty_edges=True):
    used = ws.UsedRange
    first_row = used.Row
    first_col = used.Column
    last_row = first_row + used.Rows.Count - 1
    last_col = first_col + used.Columns.Count - 1

    if not shrink_empty_edges:
        return first_row, first_col, last_row, last_col

    values = normalize_2d(ws.Range(ws.Cells(first_row, first_col), ws.Cells(last_row, last_col)).Value)
    populated = []
    for row_index, row in enumerate(values):
        for col_index, value in enumerate(row):
            if has_value(value):
                populated.append((first_row + row_index, first_col + col_index))

    if not populated:
        raise ValueError(f"工作表 {ws.Name} 没有可截图的非空单元格")

    rows = [item[0] for item in populated]
    cols = [item[1] for item in populated]
    return min(rows), min(cols), max(rows), max(cols)


def range_from_capture(ws, capture):
    capture = capture or {}
    mode = capture.get("mode", "used_range")

    if mode == "explicit_range":
        address = capture.get("range")
        if not address:
            raise ValueError("capture.mode=explicit_range 时必须提供 capture.range")
        return ws.Range(address)

    if mode == "current_region":
        return ws.Range(capture.get("start_cell", "A1")).CurrentRegion

    if mode != "used_range":
        raise ValueError(f"不支持的截图模式: {mode}")

    first_row, first_col, last_row, last_col = used_range_bounds(
        ws,
        shrink_empty_edges=capture.get("shrink_empty_edges", True),
    )
    padding_rows = int(capture.get("padding_rows", 0) or 0)
    padding_cols = int(capture.get("padding_cols", 0) or 0)
    return ws.Range(
        ws.Cells(max(1, first_row - padding_rows), max(1, first_col - padding_cols)),
        ws.Cells(last_row + padding_rows, last_col + padding_cols),
    )


def refresh_workbook(excel, workbook, timeout_seconds=120):
    workbook.RefreshAll()
    try:
        excel.CalculateUntilAsyncQueriesDone()
    except Exception:
        pass

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        refreshing = False
        try:
            for index in range(1, workbook.Connections.Count + 1):
                connection = workbook.Connections(index)
                try:
                    if connection.OLEDBConnection.Refreshing:
                        refreshing = True
                except Exception:
                    pass
                try:
                    if connection.ODBCConnection.Refreshing:
                        refreshing = True
                except Exception:
                    pass
        except Exception:
            break
        if not refreshing:
            return
        time.sleep(1)

    raise TimeoutError(f"刷新查询和连接超时: {timeout_seconds} 秒")


def capture_range_to_png(ws, output_path, capture=None):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    capture = capture or {}
    rng = range_from_capture(ws, capture)

    appearance_name = capture.get("appearance", "printer")
    format_name = capture.get("format", "picture")
    if appearance_name not in COPY_APPEARANCE:
        raise ValueError(f"不支持的 CopyPicture appearance: {appearance_name}")
    if format_name not in COPY_FORMAT:
        raise ValueError(f"不支持的 CopyPicture format: {format_name}")

    rng.CopyPicture(Appearance=COPY_APPEARANCE[appearance_name], Format=COPY_FORMAT[format_name])
    export_scale = float(capture.get("export_scale", 1) or 1)
    if export_scale <= 0:
        raise ValueError("capture.export_scale 必须大于 0")
    width = max(float(rng.Width) * export_scale, 100.0)
    height = max(float(rng.Height) * export_scale, 50.0)
    chart_object = ws.ChartObjects().Add(float(rng.Left), float(rng.Top), width, height)
    try:
        chart_object.Activate()
        chart = chart_object.Chart
        chart.Paste()
        chart.Export(str(output.resolve()), "PNG")
    finally:
        chart_object.Delete()

    optimize_png(output, capture)

    try:
        address = rng.Address(False, False)
    except TypeError:
        address = str(rng.Address)

    return {
        "path": str(output.resolve()),
        "sheet": ws.Name,
        "range": address,
        "width": width,
        "height": height,
        "appearance": appearance_name,
        "format": format_name,
        "export_scale": export_scale,
        "optimize_png": bool(capture.get("optimize_png", False)),
        "png_colors": capture.get("png_colors", 256),
    }


def optimize_png(image_path, capture):
    if not capture.get("optimize_png", False):
        return

    try:
        from PIL import Image
    except ImportError:
        return

    path = Path(image_path)
    png_colors = int(capture.get("png_colors", 256) or 256)
    png_colors = max(2, min(256, png_colors))
    with Image.open(path) as image:
        source = image.convert("RGB")
        optimized = source.quantize(colors=png_colors, method=Image.Quantize.MEDIANCUT)
        if "transparency" in optimized.info and isinstance(optimized.info["transparency"], tuple):
            del optimized.info["transparency"]
        optimized.save(path, format="PNG", optimize=True, compress_level=9)


def read_text_from_sheet(ws, text_config=None):
    text_config = text_config or {}
    mode = text_config.get("mode", "used_range")

    if mode == "explicit_range":
        address = text_config.get("range")
        if not address:
            raise ValueError("text.mode=explicit_range 时必须提供 text.range")
        rng = ws.Range(address)
    elif mode == "current_region":
        rng = ws.Range(text_config.get("start_cell", "A1")).CurrentRegion
    elif mode == "used_range":
        first_row, first_col, last_row, last_col = used_range_bounds(
            ws,
            shrink_empty_edges=text_config.get("shrink_empty_edges", True),
        )
        rng = ws.Range(ws.Cells(first_row, first_col), ws.Cells(last_row, last_col))
    else:
        raise ValueError(f"不支持的文字读取模式: {mode}")

    values = normalize_2d(rng.Value)
    lines = []
    for row in values:
        cells = [str(value).strip() for value in row if has_value(value)]
        if cells:
            lines.append("\t".join(cells))
    return "\n".join(lines).strip()


def image_payload_from_file(image_path):
    image_bytes = Path(image_path).read_bytes()
    return {
        "base64": base64.b64encode(image_bytes).decode("ascii"),
        "md5": hashlib.md5(image_bytes).hexdigest(),
    }


def build_preview_image(package, preview_file):
    image_paths = [
        Path(item["capture"]["path"])
        for item in package.get("items", [])
        if item.get("type") == "image" and item.get("capture", {}).get("path")
    ]
    if not image_paths:
        return None

    try:
        from PIL import Image
    except ImportError:
        return None

    images = [Image.open(path).convert("RGB") for path in image_paths]
    try:
        padding = 24
        background = (255, 255, 255)
        width = max(image.width for image in images)
        height = sum(image.height for image in images) + padding * (len(images) - 1)
        preview = Image.new("RGB", (width, height), background)

        top = 0
        for image in images:
            left = (width - image.width) // 2
            preview.paste(image, (left, top))
            top += image.height + padding

        preview_file.parent.mkdir(parents=True, exist_ok=True)
        preview.save(preview_file)
        return str(preview_file.resolve())
    finally:
        for image in images:
            image.close()


def item_output_name(workbook_index, report_index, item_index, workbook_name, report_name, sheet_name):
    safe_parts = [
        f"{workbook_index:02d}",
        f"{report_index:02d}",
        f"{item_index:02d}",
        workbook_name,
        report_name,
        sheet_name,
    ]
    raw = "_".join(str(part) for part in safe_parts if part)
    for char in '<>:"/\\|?*':
        raw = raw.replace(char, "_")
    return raw[:160] + ".png"


def save_package(package_file, package):
    package_file.parent.mkdir(parents=True, exist_ok=True)
    with package_file.open("w", encoding="utf-8") as f:
        json.dump(package, f, ensure_ascii=False, indent=2)


def build_message_package(config, base_dir=PROJECT_DIR, visible=False):
    image_dir, package_file, preview_file = get_output_paths(config, base_dir)
    default_capture = config.get("capture_defaults", {})
    workbooks = config.get("workbooks", [])
    if not workbooks:
        raise ValueError("配置中缺少 workbooks[]")

    package = {
        "generated_at": datetime.now().isoformat(),
        "items": [],
    }

    excel = open_excel(visible=visible)
    try:
        for workbook_index, workbook_config in enumerate(workbooks, start=1):
            workbook_name = workbook_config.get("name", f"workbook-{workbook_index}")
            workbook_path = resolve_path(workbook_config["file"], base_dir)
            open_config = workbook_config.get("excel_open", {})

            workbook = None
            try:
                workbook = open_workbook(
                    excel,
                    workbook_path,
                    update_links=open_config.get("update_links", False),
                    read_only=True,
                )

                if open_config.get("refresh_before_capture", False):
                    timeout = int(open_config.get("refresh_timeout_seconds", 120) or 120)
                    refresh_workbook(excel, workbook, timeout_seconds=timeout)

                for report_index, report in enumerate(workbook_config.get("reports", []), start=1):
                    report_name = report.get("name", f"report-{report_index}")
                    for item_index, item in enumerate(report.get("items", []), start=1):
                        item_type = item.get("type")
                        sheet_name = item.get("sheet")
                        if item_type not in {"image", "text"}:
                            raise ValueError(f"{workbook_name}/{report_name} 存在不支持的 item.type: {item_type}")
                        if not sheet_name:
                            raise ValueError(f"{workbook_name}/{report_name} 的 item 缺少 sheet")

                        ws = get_sheet(workbook, sheet_name)
                        package_item = {
                            "workbook": workbook_name,
                            "workbook_file": str(workbook_path),
                            "report": report_name,
                            "item_index": item_index,
                            "type": item_type,
                            "sheet": sheet_name,
                        }

                        if item_type == "image":
                            capture_config = merge_capture_config(default_capture, item.get("capture"))
                            image_name = item_output_name(
                                workbook_index,
                                report_index,
                                item_index,
                                workbook_name,
                                report_name,
                                sheet_name,
                            )
                            image_path = image_dir / image_name
                            capture_result = capture_range_to_png(ws, image_path, capture_config)
                            image_payload = image_payload_from_file(capture_result["path"])
                            package_item["capture"] = capture_result
                            package_item["image"] = image_payload
                        else:
                            text = read_text_from_sheet(ws, item.get("text"))
                            if not text:
                                raise ValueError(f"{workbook_name}/{report_name}/{sheet_name} 未读取到文字")
                            package_item["text"] = text
                            package_item["text_length"] = len(text)

                        package["items"].append(package_item)
                        save_package(package_file, package)
            finally:
                if workbook is not None:
                    workbook.Close(SaveChanges=False)
    finally:
        excel.Quit()

    preview_path = build_preview_image(package, preview_file)
    if preview_path:
        package["preview_image_file"] = preview_path
    save_package(package_file, package)
    return package, str(package_file)
