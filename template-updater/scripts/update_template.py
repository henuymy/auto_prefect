"""Update a temporary template copy after report comparison succeeds."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG_PATH = SKILL_DIR / "config.json"


def require_win32():
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise RuntimeError("缺少 pywin32，无法通过 Excel COM 更新模板") from exc
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


def get_sheet(workbook, sheet_name):
    try:
        return workbook.Worksheets(sheet_name)
    except Exception as exc:
        names = [workbook.Worksheets(i).Name for i in range(1, workbook.Worksheets.Count + 1)]
        raise KeyError(f"找不到工作表 {sheet_name!r}，当前工作表: {names}") from exc


def load_config(config_path=None):
    path = Path(config_path).resolve() if config_path else DEFAULT_CONFIG_PATH
    config, resolved = load_json(path)
    return config, resolved


def load_compare_context(config, config_dir):
    compare_result_path = resolve_path(config_dir, config.get("compare_result_path"))
    if not compare_result_path:
        raise ValueError("缺少 compare_result_path")
    compare_result, _ = load_json(compare_result_path)

    compare_config = {}
    compare_config_path = resolve_path(config_dir, config.get("compare_config_path"))
    compare_config_dir = None
    if compare_config_path and compare_config_path.exists():
        compare_config, compare_config_path = load_json(compare_config_path)
        compare_config_dir = compare_config_path.parent

    source_report_path = resolve_path(config_dir, config.get("source_report_path"))
    template_path = resolve_path(config_dir, config.get("template_path"))
    if not source_report_path and compare_config:
        source_report_path = resolve_path(compare_config_dir, compare_config.get("new_report_path"))
    if not template_path and compare_config:
        template_path = resolve_path(compare_config_dir, compare_config.get("template_path"))

    if not source_report_path:
        raise ValueError("缺少 source_report_path，也无法从 compare_config_path 中读取 new_report_path")
    if not template_path:
        raise ValueError("缺少 template_path，也无法从 compare_config_path 中读取 template_path")
    return compare_result, source_report_path, template_path


def should_write_sheet(sheet_result, write_sheets):
    result = sheet_result.get("result")
    if write_sheets == "all_compared":
        return result in {"same", "changed"}
    if write_sheets == "changed":
        return result == "changed"
    raise ValueError("write_sheets 只支持 changed 或 all_compared")


def output_template_path(template_path, output_dir):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return output_dir / f"{template_path.stem}_updated_{timestamp}{template_path.suffix}"


def clear_target_sheet(sheet):
    used = sheet.UsedRange
    used.Clear()


def copy_sheet_content(source_sheet, target_sheet):
    clear_target_sheet(target_sheet)
    source_sheet.UsedRange.Copy()
    target_sheet.Range("A1").PasteSpecial(Paste=-4104)


def update_template_copy(source_report_path, template_path, output_path, sheet_results, write_sheets, visible=False):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_path, output_path)

    win32com = require_win32()
    excel = win32com.DispatchEx("Excel.Application")
    excel.Visible = bool(visible)
    excel.DisplayAlerts = False
    excel.AskToUpdateLinks = False
    source_workbook = None
    target_workbook = None
    updated_sheets = []
    try:
        source_workbook = excel.Workbooks.Open(
            str(source_report_path),
            UpdateLinks=0,
            ReadOnly=True,
            IgnoreReadOnlyRecommended=True,
        )
        target_workbook = excel.Workbooks.Open(
            str(output_path),
            UpdateLinks=0,
            ReadOnly=False,
            IgnoreReadOnlyRecommended=True,
        )

        for sheet_result in sheet_results:
            if not should_write_sheet(sheet_result, write_sheets):
                continue
            source_sheet_name = sheet_result["new_sheet_name"]
            target_sheet_name = sheet_result["template_sheet_name"]
            source_sheet = get_sheet(source_workbook, source_sheet_name)
            target_sheet = get_sheet(target_workbook, target_sheet_name)
            copy_sheet_content(source_sheet, target_sheet)
            updated_sheets.append({
                "name": sheet_result.get("name") or source_sheet_name,
                "source_sheet_name": source_sheet_name,
                "template_sheet_name": target_sheet_name,
                "compare_result": sheet_result.get("result"),
            })

        target_workbook.Save()
    finally:
        try:
            excel.CutCopyMode = False
        except Exception:
            pass
        if source_workbook is not None:
            source_workbook.Close(SaveChanges=False)
        if target_workbook is not None:
            target_workbook.Close(SaveChanges=False)
        excel.Quit()
    return updated_sheets


def write_manifest(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def run(config_path=None):
    config, config_path = load_config(config_path)
    config_dir = config_path.parent
    compare_result, source_report_path, template_path = load_compare_context(config, config_dir)

    manifest_path = resolve_path(config_dir, config.get("manifest_path", "runtime/update_manifest.json"))
    output_dir = resolve_path(config_dir, config.get("output_dir", "runtime/templates"))
    write_sheets = config.get("write_sheets", "changed")

    result = compare_result.get("result")
    if result == "invalid":
        raise RuntimeError("比对结果为 invalid，停止更新模板")
    if result == "same":
        manifest = {
            "status": "skipped",
            "reason": "compare_result_same",
            "source_report_path": str(source_report_path),
            "template_path": str(template_path),
            "output_path": None,
            "updated_sheets": [],
            "generated_at": datetime.now().isoformat(),
        }
        write_manifest(manifest_path, manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return manifest
    if result != "changed":
        raise RuntimeError(f"不支持的比对结果: {result}")

    output_path = output_template_path(template_path, output_dir)
    updated_sheets = update_template_copy(
        source_report_path,
        template_path,
        output_path,
        compare_result.get("sheets", []),
        write_sheets,
        visible=bool(config.get("visible", False)),
    )
    manifest = {
        "status": "updated",
        "source_report_path": str(source_report_path),
        "template_path": str(template_path),
        "output_path": str(output_path),
        "write_sheets": write_sheets,
        "updated_sheets": updated_sheets,
        "generated_at": datetime.now().isoformat(),
    }
    write_manifest(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main():
    parser = argparse.ArgumentParser(description="比对通过后更新临时模板副本。")
    parser.add_argument("--config", default=None, help=f"配置文件路径，默认 {DEFAULT_CONFIG_PATH}")
    args = parser.parse_args()
    run(args.config)


if __name__ == "__main__":
    main()
