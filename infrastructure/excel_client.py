"""Excel client utilities."""

from __future__ import annotations


def require_win32():
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise RuntimeError("缺少 pywin32，请先安装: pip install pywin32") from exc
    return win32com.client


def open_excel(visible=False):
    win32com = require_win32()
    excel = win32com.DispatchEx("Excel.Application")
    excel.Visible = bool(visible)
    excel.DisplayAlerts = False
    excel.AskToUpdateLinks = False
    return excel


def open_workbook(excel, path, update_links=False, read_only=True):
    from pathlib import Path
    return excel.Workbooks.Open(
        str(Path(path).resolve()),
        UpdateLinks=3 if update_links else 0,
        ReadOnly=read_only,
    )


def get_sheet(workbook, sheet_name):
    try:
        return workbook.Worksheets(sheet_name)
    except Exception as exc:
        names = [workbook.Worksheets(i).Name for i in range(1, workbook.Worksheets.Count + 1)]
        raise KeyError(f"找不到工作表 {sheet_name!r}，当前工作表: {names}") from exc


def sheet_names(workbook):
    return [workbook.Worksheets(i).Name for i in range(1, workbook.Worksheets.Count + 1)]
