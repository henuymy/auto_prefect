"""Excel client utilities."""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

XL_CALCULATION_MANUAL = -4135
DEFAULT_EXCEL_LOCK_PATH = Path(__file__).resolve().parents[1] / "runtime" / "locks" / "excel_com.lock"
DEFAULT_EXCEL_LOCK_TIMEOUT_SECONDS = 900
DEFAULT_EXCEL_LOCK_POLL_SECONDS = 2


class ExcelComLockTimeout(RuntimeError):
    pass


class FileLock:
    def __init__(self, path=DEFAULT_EXCEL_LOCK_PATH, timeout_seconds=DEFAULT_EXCEL_LOCK_TIMEOUT_SECONDS, poll_seconds=DEFAULT_EXCEL_LOCK_POLL_SECONDS):
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.handle = self.path.open("x", encoding="utf-8")
                self.handle.write(f"pid={_current_pid()} acquired_at={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                self.handle.flush()
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    lock_info = ""
                    try:
                        lock_info = self.path.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass
                    raise ExcelComLockTimeout(f"等待 Excel COM 锁超时: {self.path}, lock_info={lock_info}")
                time.sleep(self.poll_seconds)

    def release(self):
        if self.handle is not None:
            try:
                self.handle.close()
            finally:
                self.handle = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class LockedExcel:
    def __init__(self, excel, lock):
        self._excel = excel
        self._lock = lock
        self._released = False

    def __getattr__(self, name):
        return getattr(self._excel, name)

    def Quit(self):
        try:
            return self._excel.Quit()
        finally:
            self.release_lock()

    def release_lock(self):
        if self._released:
            return
        self._released = True
        self._lock.release()


def _current_pid():
    try:
        import os
        return os.getpid()
    except Exception:
        return "unknown"


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


def open_excel(visible=False, manual_calculation=False, use_lock=True):
    win32com = require_win32()
    lock = FileLock().acquire() if use_lock else None
    try:
        try:
            excel = win32com.DispatchEx("Excel.Application")
        except AttributeError as exc:
            if not is_broken_gencache_error(exc):
                raise
            clear_win32com_gencache(win32com)
            excel = dispatch_excel_dynamic(win32com)
        safe_configure_excel(excel, visible=visible, manual_calculation=manual_calculation)
        return LockedExcel(excel, lock) if lock else excel
    except Exception:
        if lock is not None:
            lock.release()
        raise


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
