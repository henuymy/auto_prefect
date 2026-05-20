"""Excel client utilities."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

XL_CALCULATION_MANUAL = -4135


def require_win32():
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise RuntimeError("缺少 pywin32，请先安装: pip install pywin32") from exc
    return win32com.client


def try_set_excel_property(excel, name, value):
    try:
        setattr(excel, name, value)
        return True
    except Exception:
        return False


def safe_configure_excel(excel, visible=False, manual_calculation=False):
    applied = {}
    applied["Visible"] = try_set_excel_property(excel, "Visible", bool(visible))
    applied["DisplayAlerts"] = try_set_excel_property(excel, "DisplayAlerts", False)
    applied["AskToUpdateLinks"] = try_set_excel_property(excel, "AskToUpdateLinks", False)
    applied["ScreenUpdating"] = try_set_excel_property(excel, "ScreenUpdating", False)
    applied["EnableEvents"] = try_set_excel_property(excel, "EnableEvents", False)
    if manual_calculation:
        applied["Calculation"] = try_set_excel_property(excel, "Calculation", XL_CALCULATION_MANUAL)
    return applied


def is_broken_gencache_error(exc):
    text = str(exc)
    return "CLSIDToClassMap" in text or "win32com.gen_py" in text


def clear_win32com_gencache(win32com):
    try:
        from win32com.client import gencache  # type: ignore
    except Exception:
        return None

    cache_path = Path(gencache.GetGeneratePath())
    for module_name in list(sys.modules):
        if module_name.startswith("win32com.gen_py"):
            sys.modules.pop(module_name, None)
    shutil.rmtree(cache_path, ignore_errors=True)
    try:
        gencache.is_readonly = False
        gencache.Rebuild()
    except Exception:
        pass
    return cache_path


def dispatch_excel_dynamic(win32com):
    import pythoncom  # type: ignore

    dispatch = pythoncom.CoCreateInstance(
        "Excel.Application",
        None,
        pythoncom.CLSCTX_SERVER,
        pythoncom.IID_IDispatch,
    )
    return win32com.dynamic.Dispatch(dispatch)


def open_excel(visible=False, manual_calculation=False):
    win32com = require_win32()
    try:
        excel = win32com.DispatchEx("Excel.Application")
    except AttributeError as exc:
        if not is_broken_gencache_error(exc):
            raise
        clear_win32com_gencache(win32com)
        excel = dispatch_excel_dynamic(win32com)
    safe_configure_excel(excel, visible=visible, manual_calculation=manual_calculation)
    return excel


def open_workbook(excel, path, update_links=False, read_only=True, ignore_read_only_recommended=True):
    from pathlib import Path
    return excel.Workbooks.Open(
        str(Path(path).resolve()),
        UpdateLinks=3 if update_links else 0,
        ReadOnly=read_only,
        IgnoreReadOnlyRecommended=ignore_read_only_recommended,
    )


def get_sheet(workbook, sheet_name):
    try:
        return workbook.Worksheets(sheet_name)
    except Exception as exc:
        names = [workbook.Worksheets(i).Name for i in range(1, workbook.Worksheets.Count + 1)]
        raise KeyError(f"找不到工作表 {sheet_name!r}，当前工作表: {names}") from exc


def sheet_names(workbook):
    return [workbook.Worksheets(i).Name for i in range(1, workbook.Worksheets.Count + 1)]
