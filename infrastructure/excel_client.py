"""Excel client utilities."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from services.runtime_paths import runtime_path

XL_CALCULATION_MANUAL = -4135
DEFAULT_EXCEL_LOCK_TIMEOUT_SECONDS = 900
DEFAULT_EXCEL_LOCK_POLL_SECONDS = 2
DEFAULT_EXCEL_LOCK_STALE_SECONDS = 180
DEFAULT_ORPHANED_EXCEL_MIN_AGE_SECONDS = 120


class ExcelComLockTimeout(RuntimeError):
    pass


def default_excel_lock_path() -> Path:
    return runtime_path("locks/excel_com.lock")


def _windows_process_started_at(pid: int) -> str | None:
    command = (
        f"$process = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
        "if ($null -eq $process -or $null -eq $process.StartTime) { exit 1 }; "
        "$process.StartTime.ToUniversalTime().ToString('o')"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _parse_process_time(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def process_is_running(pid: int, started_at: str | None = None) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        actual_started_at = _windows_process_started_at(pid)
        if actual_started_at is None:
            return False
        if not started_at:
            return True
        try:
            return _parse_process_time(actual_started_at) == _parse_process_time(started_at)
        except (TypeError, ValueError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, SystemError):
        return False


def current_process_started_at() -> str:
    if sys.platform == "win32":
        started_at = _windows_process_started_at(os.getpid())
        if started_at is None:
            raise RuntimeError("无法读取当前进程启动时间")
        return _parse_process_time(started_at).isoformat()
    return datetime.now(timezone.utc).isoformat()


def cleanup_orphaned_excel_processes(min_age_seconds=DEFAULT_ORPHANED_EXCEL_MIN_AGE_SECONDS):
    if sys.platform != "win32":
        return []
    min_age_seconds = max(0, int(min_age_seconds or 0))
    command = f"""
$now = Get-Date
Get-Process -Name EXCEL -ErrorAction SilentlyContinue |
  Where-Object {{
    $_.MainWindowHandle -eq 0 -and
    $_.StartTime -and
    (($now - $_.StartTime).TotalSeconds -ge {min_age_seconds})
  }} |
  ForEach-Object {{
    $item = [PSCustomObject]@{{
      Id = $_.Id
      StartTime = $_.StartTime.ToString('yyyy-MM-dd HH:mm:ss')
      MainWindowTitle = $_.MainWindowTitle
    }}
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    $item
  }} |
  ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        import json

        payload = json.loads(completed.stdout)
    except Exception:
        return []
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return payload
    return []


class FileLock:
    def __init__(
        self,
        path=None,
        timeout_seconds=DEFAULT_EXCEL_LOCK_TIMEOUT_SECONDS,
        poll_seconds=DEFAULT_EXCEL_LOCK_POLL_SECONDS,
        stale_seconds=DEFAULT_EXCEL_LOCK_STALE_SECONDS,
    ):
        self.path = Path(path) if path else default_excel_lock_path()
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.stale_seconds = stale_seconds
        self.handle = None
        self.owner_token = None

    @staticmethod
    def _owner_identity(payload, raw_content):
        if isinstance(payload, dict) and payload.get("owner_token"):
            return ("owner_token", str(payload["owner_token"]))
        metadata = payload if isinstance(payload, dict) else {}
        return (
            "fingerprint",
            metadata.get("pid"),
            metadata.get("process_started_at"),
            metadata.get("acquired_at"),
            metadata.get("created_at"),
            hashlib.sha256(raw_content).hexdigest(),
        )

    def _read_lock_snapshot(self):
        try:
            stat_before = self.path.stat()
            raw_content = self.path.read_bytes()
            stat_after = self.path.stat()
        except FileNotFoundError:
            return None
        if (
            stat_before.st_mtime_ns != stat_after.st_mtime_ns
            or stat_before.st_size != stat_after.st_size
        ):
            return None
        try:
            payload = json.loads(raw_content.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError):
            payload = {}
        return {
            "identity": self._owner_identity(payload, raw_content),
            "payload": payload,
            "mtime": stat_after.st_mtime,
        }

    def _snapshot_is_stale(self, snapshot):
        payload = snapshot["payload"]
        if isinstance(payload, dict) and payload.get("pid"):
            return not process_is_running(
                payload["pid"], payload.get("process_started_at")
            )
        return time.time() - snapshot["mtime"] > self.stale_seconds

    def is_stale(self):
        snapshot = self._read_lock_snapshot()
        return bool(snapshot and self._snapshot_is_stale(snapshot))

    def _remove_stale_lock(self, inspected_identity):
        current = self._read_lock_snapshot()
        if current is None or current["identity"] != inspected_identity:
            return "retry"
        try:
            self.path.unlink()
            return "removed"
        except FileNotFoundError:
            return "retry"
        except PermissionError:
            return "permission_denied"

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.handle = self.path.open("x", encoding="utf-8")
                try:
                    self.owner_token = uuid4().hex
                    self.handle.write(
                        json.dumps(
                            {
                                "pid": _current_pid(),
                                "process_started_at": current_process_started_at(),
                                "owner_token": self.owner_token,
                                "acquired_at": datetime.now(timezone.utc).isoformat(),
                            },
                            ensure_ascii=False,
                        )
                    )
                    self.handle.flush()
                except Exception:
                    self.handle.close()
                    self.handle = None
                    self.owner_token = None
                    self.path.unlink(missing_ok=True)
                    raise
                return self
            except FileExistsError:
                snapshot = self._read_lock_snapshot()
                if snapshot and self._snapshot_is_stale(snapshot):
                    removal = self._remove_stale_lock(snapshot["identity"])
                    if removal in {"removed", "retry"}:
                        continue
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
        if not self.owner_token:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(payload, dict) or payload.get("owner_token") != self.owner_token:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self.owner_token = None


class LockedExcel:
    def __init__(self, excel, lock, pythoncom_module=None):
        self._excel = excel
        self._lock = lock
        self._pythoncom = pythoncom_module
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
        if self._lock is not None:
            self._lock.release()
        if self._pythoncom is not None:
            self._pythoncom.CoUninitialize()
            self._pythoncom = None


def _current_pid():
    return os.getpid()


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


def open_excel(visible=False, manual_calculation=False, use_lock=True, cleanup_orphaned=False):
    win32com = require_win32()
    try:
        import pythoncom  # type: ignore
    except ImportError as exc:
        raise RuntimeError("缺少 pywin32，请先安装: pip install pywin32") from exc
    pythoncom.CoInitialize()
    lock = FileLock().acquire() if use_lock else None
    try:
        if cleanup_orphaned:
            cleanup_orphaned_excel_processes()
        try:
            excel = win32com.DispatchEx("Excel.Application")
        except AttributeError as exc:
            if not is_broken_gencache_error(exc):
                raise
            clear_win32com_gencache(win32com)
            excel = dispatch_excel_dynamic(win32com)
        safe_configure_excel(excel, visible=visible, manual_calculation=manual_calculation)
        return LockedExcel(excel, lock, pythoncom)
    except Exception:
        if lock is not None:
            lock.release()
        pythoncom.CoUninitialize()
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
