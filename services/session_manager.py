"""Session and Cookie management service."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import requests

from services.browser_session import (
    BrowserSessionCleanupError,
    browser_cleanup_succeeded,
    close_browser_session,
    format_browser_close_result,
    summarize_browser_close_result,
)
from services.json_excel_service import get_by_path
from services.method_service import (
    AuthenticationMaterialMissingError,
    build_cookie_jar,
    build_headers,
    resolve_storage_references,
)
from services.runtime_paths import resolve_runtime_relative_path
from utils.date_placeholders import resolve_dynamic_structure


PROJECT_DIR = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)
DEFAULT_LOCK_STALE_SECONDS = 30 * 60
DEFAULT_LOGIN_TIMEOUT_SECONDS = 10 * 60
LOGIN_ENVELOPE_OVERHEAD_SECONDS = 2 * 60
DEFAULT_LOCK_WAIT_SECONDS = (
    2 * DEFAULT_LOGIN_TIMEOUT_SECONDS + 60 + LOGIN_ENVELOPE_OVERHEAD_SECONDS + 60
)
DEFAULT_LOCK_POLL_SECONDS = 5
SESSION_EXPIRED_RE_CODES = {"1101", "401", "403"}
SESSION_EXPIRED_URL_KEYWORDS = ("login.jsp", "logout.action", "kickedout")
SESSION_EXPIRED_BODY_KEYWORDS = ("单点登录超时", "请登录", "logging down", "login.jsp")
_LOCAL_FILE_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_FILE_LOCKS_GUARD = threading.Lock()


def _local_file_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCAL_FILE_LOCKS_GUARD:
        return _LOCAL_FILE_LOCKS.setdefault(key, threading.Lock())


def resolve_path(value, base_dir=PROJECT_DIR):
    if not value:
        return None
    path = Path(value)
    return path.resolve() if path.is_absolute() else resolve_runtime_relative_path(path)


def load_json(path):
    resolved = Path(path).resolve()
    with resolved.open("r", encoding="utf-8") as f:
        return json.load(f), resolved


def write_json(path, payload):
    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return resolved


def read_lock_info(lock_path):
    try:
        with Path(lock_path).open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _windows_process_started_at(pid: int) -> str | None:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"$p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; if ($p) {{$p.StartTime.ToUniversalTime().ToString('o')}}",
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _parse_process_time(value):
    return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def current_process_started_at():
    if os.name == "nt":
        value = _windows_process_started_at(os.getpid())
        if value is None:
            raise RuntimeError("无法读取当前进程启动时间")
        return _parse_process_time(value).isoformat()
    return datetime.now(timezone.utc).isoformat()


def process_is_running(pid, started_at=None):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        actual_started_at = _windows_process_started_at(pid)
        if actual_started_at is None:
            return False
        if not started_at:
            return True
        try:
            return _parse_process_time(actual_started_at) == _parse_process_time(
                started_at
            )
        except (TypeError, ValueError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, SystemError):
        return False


def lock_is_stale(lock_path, stale_seconds):
    snapshot = _read_lock_snapshot(Path(lock_path))
    return bool(snapshot and _lock_snapshot_is_stale(snapshot, stale_seconds))


def _lock_owner_identity(payload, raw_content):
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


def _read_lock_snapshot(lock_path):
    lock_path = Path(lock_path)
    try:
        stat_before = lock_path.stat()
        raw_content = lock_path.read_bytes()
        stat_after = lock_path.stat()
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
        "identity": _lock_owner_identity(payload, raw_content),
        "payload": payload,
        "mtime": stat_after.st_mtime,
    }


def _lock_snapshot_is_stale(snapshot, stale_seconds):
    payload = snapshot["payload"]
    if isinstance(payload, dict) and payload.get("pid"):
        started_at = payload.get("process_started_at")
        if started_at:
            return not process_is_running(payload["pid"], started_at)
        return not process_is_running(payload["pid"])
    return time.time() - snapshot["mtime"] > stale_seconds


def _remove_stale_lock(lock_path, inspected_identity):
    lock_path = Path(lock_path)
    current = _read_lock_snapshot(lock_path)
    if current is None or current["identity"] != inspected_identity:
        return "retry"
    try:
        lock_path.unlink()
        return "removed"
    except FileNotFoundError:
        return "retry"
    except PermissionError:
        return "permission_denied"


def _unlink_lock_file(lock_path: Path, *, retry_seconds: float = 2.0) -> None:
    """Remove a lock file despite transient Windows handle retention."""
    deadline = time.monotonic() + max(0.0, retry_seconds)
    while True:
        try:
            lock_path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _try_acquire_os_lock(handle):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _release_os_lock(handle):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LoginFileLock:
    def __init__(
        self,
        lock_path,
        *,
        wait_seconds=DEFAULT_LOCK_WAIT_SECONDS,
        poll_seconds=DEFAULT_LOCK_POLL_SECONDS,
        stale_seconds=DEFAULT_LOCK_STALE_SECONDS,
        lock_label="登录锁",
    ):
        self.path = Path(lock_path).resolve()
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds
        self.stale_seconds = stale_seconds
        self.lock_label = lock_label
        self.owner_token = None
        self.waited = False
        self.guard_handle = None
        self.guard_acquired = False

    def _release_guard(self):
        if self.guard_handle is None:
            return
        try:
            if self.guard_acquired:
                _release_os_lock(self.guard_handle)
        finally:
            self.guard_handle.close()
            self.guard_handle = None
            self.guard_acquired = False

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.wait_seconds
        guard_path = self.path.with_name(f"{self.path.name}.guard")
        self.guard_handle = guard_path.open("a+b")
        if self.guard_handle.seek(0, os.SEEK_END) == 0:
            self.guard_handle.write(b"\0")
            self.guard_handle.flush()
        while True:
            if not _try_acquire_os_lock(self.guard_handle):
                self.waited = True
            else:
                self.guard_acquired = True
                try:
                    snapshot = _read_lock_snapshot(self.path)
                    if snapshot is not None:
                        if not _lock_snapshot_is_stale(
                            snapshot, self.stale_seconds
                        ):
                            self._release_guard()
                            self.guard_handle = guard_path.open("a+b")
                            self.waited = True
                        else:
                            removal = _remove_stale_lock(
                                self.path, snapshot["identity"]
                            )
                            if removal == "permission_denied":
                                self._release_guard()
                                self.guard_handle = guard_path.open("a+b")
                                self.waited = True
                    if self.guard_handle is not None and not self.path.exists():
                        fd = os.open(
                            str(self.path),
                            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        )
                    else:
                        fd = None
                    if fd is None:
                        if self.guard_acquired:
                            self._release_guard()
                            self.guard_handle = guard_path.open("a+b")
                        self.waited = True
                    else:
                        self.owner_token = uuid4().hex
                        with os.fdopen(fd, "w", encoding="utf-8") as handle:
                            json.dump(
                                {
                                    "pid": os.getpid(),
                                    "process_started_at": current_process_started_at(),
                                    "owner_token": self.owner_token,
                                    "acquired_at": datetime.now(timezone.utc).isoformat(),
                                },
                                handle,
                                ensure_ascii=False,
                                indent=2,
                            )
                        return self
                except Exception:
                    self.owner_token = None
                    self.path.unlink(missing_ok=True)
                    self._release_guard()
                    raise
            if time.monotonic() >= deadline:
                self._release_guard()
                raise TimeoutError(f"等待{self.lock_label}超时: {self.path}")
            time.sleep(self.poll_seconds)

    def release(self):
        try:
            if not self.owner_token:
                return
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return
            if (
                not isinstance(payload, dict)
                or payload.get("owner_token") != self.owner_token
            ):
                return
            _unlink_lock_file(self.path)
        finally:
            self.owner_token = None
            self._release_guard()


@contextmanager
def _cross_process_file_lock(
    lock_path,
    wait_seconds=DEFAULT_LOCK_WAIT_SECONDS,
    poll_seconds=DEFAULT_LOCK_POLL_SECONDS,
    stale_seconds=DEFAULT_LOCK_STALE_SECONDS,
    lock_label="登录锁",
):
    lock = LoginFileLock(
        lock_path,
        wait_seconds=wait_seconds,
        poll_seconds=poll_seconds,
        stale_seconds=stale_seconds,
        lock_label=lock_label,
    ).acquire()
    try:
        yield {"lock_path": str(lock.path), "waited": lock.waited}
    finally:
        lock.release()


@contextmanager
def file_lock(
    lock_path,
    wait_seconds=DEFAULT_LOCK_WAIT_SECONDS,
    poll_seconds=DEFAULT_LOCK_POLL_SECONDS,
    stale_seconds=DEFAULT_LOCK_STALE_SECONDS,
    lock_label="登录锁",
):
    resolved_path = Path(lock_path).resolve()
    local_lock = _local_file_lock(resolved_path)
    if not local_lock.acquire(timeout=max(0.0, float(wait_seconds))):
        raise TimeoutError(f"等待{lock_label}线程锁超时: {resolved_path}")
    try:
        with _cross_process_file_lock(
            resolved_path,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
            stale_seconds=stale_seconds,
            lock_label=lock_label,
        ) as result:
            yield result
    finally:
        local_lock.release()


def stage_names(cookie_dump):
    return [stage.get("stage") for stage in cookie_dump.get("stages", []) if stage.get("stage")]


def find_stage(cookie_dump, stage_name):
    for stage in cookie_dump.get("stages", []):
        if stage.get("stage") == stage_name:
            return stage
    return None


def cookie_is_expired(cookie, now=None, min_ttl_seconds=0):
    expiry = cookie.get("expiry")
    if not expiry:
        return False
    now = now or datetime.now(timezone.utc).timestamp()
    return float(expiry) <= now + min_ttl_seconds


def stage_has_live_cookies(stage, min_ttl_seconds=0):
    cookies = stage.get("cookies") or []
    if not cookies:
        return False
    return any(not cookie_is_expired(cookie, min_ttl_seconds=min_ttl_seconds) for cookie in cookies)


def cookie_dump_age_seconds(cookie_dump):
    generated_at = cookie_dump.get("generated_at")
    if not generated_at:
        return None
    try:
        ts = datetime.fromisoformat(generated_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except ValueError:
        return None


def validate_cookie_dump(cookie_dump, required_stages=None, min_ttl_seconds=0, max_age_seconds=None):
    required_stages = required_stages or []
    missing = []
    expired_or_empty = []
    for stage_name in required_stages:
        stage = find_stage(cookie_dump, stage_name)
        if not stage:
            missing.append(stage_name)
            continue
        if not stage_has_live_cookies(stage, min_ttl_seconds=min_ttl_seconds):
            expired_or_empty.append(stage_name)

    too_old = False
    if max_age_seconds is not None:
        age = cookie_dump_age_seconds(cookie_dump)
        if age is None or age > max_age_seconds:
            too_old = True

    valid = not missing and not expired_or_empty and not too_old
    return {
        "valid": valid,
        "available_stages": stage_names(cookie_dump),
        "missing_stages": missing,
        "expired_or_empty_stages": expired_or_empty,
        "too_old": too_old,
    }


def response_has_session_expired(response, payload=None):
    lowered_url = str(getattr(response, "url", "") or "").lower()
    if any(keyword in lowered_url for keyword in SESSION_EXPIRED_URL_KEYWORDS):
        return True
    headers = getattr(response, "headers", {}) or {}
    lowered_location = str(headers.get("Location") or headers.get("location") or "").lower()
    if any(keyword in lowered_location for keyword in SESSION_EXPIRED_URL_KEYWORDS):
        return True
    if getattr(response, "status_code", None) in {401, 403}:
        return True

    body = str(getattr(response, "text", "") or "")
    lowered_body = body.lower()
    if any(keyword in lowered_body for keyword in SESSION_EXPIRED_BODY_KEYWORDS):
        return True

    if isinstance(payload, dict):
        re_code = str(payload.get("reCode") or payload.get("status") or "")
        re_msg = str(payload.get("reMsg") or payload.get("message") or "")
        if re_code in SESSION_EXPIRED_RE_CODES:
            return True
        if "登录" in re_msg or "超时" in re_msg:
            return True
    return False


def response_json_or_none(response):
    try:
        return response.json()
    except ValueError:
        return None


def build_probe_request_kwargs(probe, stage):
    timeout_seconds = int(probe.get("timeout_seconds", 8) or 8)
    connect_timeout_seconds = probe.get("connect_timeout_seconds")
    read_timeout_seconds = probe.get("read_timeout_seconds")
    if connect_timeout_seconds is None and read_timeout_seconds is None:
        timeout = timeout_seconds
    else:
        timeout = (
            int(connect_timeout_seconds or timeout_seconds),
            int(read_timeout_seconds or timeout_seconds),
        )
    kwargs = {
        "headers": build_headers(probe, stage),
        "params": resolve_probe_value(probe.get("params"), stage) or None,
        "timeout": timeout,
        "verify": bool(probe.get("verify_ssl", False)),
        "allow_redirects": bool(probe.get("allow_redirects", False)),
    }
    body_type = str(probe.get("body_type") or "").strip().lower()
    if body_type == "json":
        kwargs["json"] = resolve_probe_value(probe.get("data", {}), stage)
    elif body_type == "form":
        kwargs["data"] = resolve_probe_value(probe.get("data", {}), stage)
    elif body_type == "raw":
        kwargs["data"] = resolve_probe_value(probe.get("raw_body", ""), stage)
    elif body_type:
        raise ValueError(f"probe body_type 只支持 form/json/raw: {body_type}")
    return kwargs


def resolve_probe_value(value, stage):
    return resolve_dynamic_structure(resolve_storage_references(value, stage))


def execute_stage_probe(stage_name, probe, stage):
    session = requests.Session()
    session.trust_env = bool(probe.get("trust_env", False))
    session.cookies.update(build_cookie_jar(stage, cookie_names=probe.get("cookie_names")))
    session.headers.update({"User-Agent": "session-probe/1.0"})
    method = str(probe.get("method") or "GET").upper()
    request_kwargs = build_probe_request_kwargs(probe, stage)
    retry_delay_seconds = probe.get("retry_delay_seconds", 0.5)
    if isinstance(retry_delay_seconds, bool):
        raise ValueError("probe retry_delay_seconds 必须是非负数")
    try:
        retry_delay_seconds = float(retry_delay_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("probe retry_delay_seconds 必须是非负数") from exc
    if retry_delay_seconds < 0:
        raise ValueError("probe retry_delay_seconds 必须是非负数")
    for attempt in range(2):
        try:
            response = session.request(method, probe["url"], **request_kwargs)
            break
        except requests.exceptions.RequestException as exc:
            if attempt == 1:
                return {
                    "stage": stage_name,
                    "enabled": True,
                    "ok": False,
                    "reason": "probe_error",
                    "error": type(exc).__name__,
                }
            time.sleep(retry_delay_seconds)

    payload = response_json_or_none(response)

    status_codes = probe.get("success_status_codes") or list(range(200, 300))
    status_ok = response.status_code in {int(code) for code in status_codes}
    expired = response_has_session_expired(response, payload=payload)
    result = {
        "stage": stage_name,
        "enabled": True,
        "url": response.url,
        "status_code": response.status_code,
        "ok": False,
    }
    if expired:
        result["reason"] = "session_expired"
        return result
    if not status_ok:
        result["reason"] = "status_not_allowed"
        return result

    success_json_path = probe.get("success_json_path")
    if success_json_path:
        if not isinstance(payload, dict):
            result["reason"] = "json_required"
            return result
        actual_value = get_by_path(payload, success_json_path)
        expected_value = probe.get("success_value")
        if expected_value is not None and str(actual_value) != str(expected_value):
            result["reason"] = "json_value_mismatch"
            result["actual_value"] = actual_value
            result["expected_value"] = expected_value
            return result

    if not response.content and not probe.get("allow_empty_body", False) and not success_json_path:
        result["reason"] = "empty_body"
        return result

    result["ok"] = True
    return result


def configured_stage_probes(stage_probe_config):
    if not stage_probe_config:
        return []
    if isinstance(stage_probe_config, list):
        return stage_probe_config
    if not isinstance(stage_probe_config, dict):
        raise ValueError("stage_probes 的 stage 配置必须是对象或对象列表")
    if stage_probe_config.get("enabled", True) is False:
        return []
    if "probes" in stage_probe_config:
        probes = stage_probe_config["probes"]
        if not isinstance(probes, list):
            raise ValueError("stage_probes.probes 必须是列表")
        return probes
    return [stage_probe_config]


def configured_stage_fallback_probes(stage_probe_config):
    if not isinstance(stage_probe_config, dict):
        return []
    fallback_probes = stage_probe_config.get("fallback_probes")
    if fallback_probes is None:
        return []
    if "probes" in stage_probe_config:
        raise ValueError("stage_probes 不能同时配置 probes 和 fallback_probes")
    if not isinstance(fallback_probes, list):
        raise ValueError("stage_probes.fallback_probes 必须是列表")
    return fallback_probes


def validate_stage_probes(cookie_dump, required_stages=None, stage_probes=None):
    required_stages = required_stages or []
    stage_probes = stage_probes or {}
    results = []
    for stage_name in required_stages:
        stage_probe_config = stage_probes.get(stage_name)
        probes = configured_stage_probes(stage_probe_config)
        fallback_probes = configured_stage_fallback_probes(stage_probe_config)
        if not probes:
            results.append({"stage": stage_name, "enabled": False, "ok": True, "reason": "no_probe"})
            continue
        stage = find_stage(cookie_dump, stage_name)
        if not stage:
            results.append({"stage": stage_name, "enabled": True, "ok": False, "reason": "missing_stage"})
            continue
        for probe in probes:
            if not isinstance(probe, dict):
                raise ValueError("stage_probes.probes 的元素必须是对象")
            if probe.get("enabled", True) is False:
                continue
            try:
                probe_result = execute_stage_probe(stage_name, probe, stage)
                if probe_result["ok"] or not fallback_probes:
                    results.append(probe_result)
                    continue

                fallback_results = []
                for fallback_probe in fallback_probes:
                    if not isinstance(fallback_probe, dict):
                        raise ValueError("stage_probes.fallback_probes 的元素必须是对象")
                    if fallback_probe.get("enabled", True) is False:
                        continue
                    fallback_result = execute_stage_probe(
                        stage_name, fallback_probe, stage
                    )
                    fallback_results.append(fallback_result)
                    if fallback_result["ok"]:
                        fallback_result["fallback_used"] = True
                        results.append(fallback_result)
                        break
                else:
                    results.append(probe_result)
                    results.extend(fallback_results)
            except AuthenticationMaterialMissingError:
                results.append(
                    {
                        "stage": stage_name,
                        "enabled": True,
                        "ok": False,
                        "reason": "missing_authentication_material",
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "stage": stage_name,
                        "enabled": True,
                        "ok": False,
                        "reason": "probe_error",
                        "error": type(exc).__name__,
                    }
                )
    return {
        "valid": all(item.get("ok") for item in results),
        "results": results,
    }


PROBE_HEALTHY = "healthy"
PROBE_AUTHENTICATION_FAILURE = "authentication"
PROBE_INFRASTRUCTURE_FAILURE = "infrastructure"
AUTHENTICATION_STATUS_CODES = {302, 401, 403}
AUTHENTICATION_FAILURE_REASONS = {
    "session_expired",
    "missing_stage",
    "missing_authentication_material",
    "json_value_mismatch",
}


class SessionInfrastructureError(RuntimeError):
    pass


MAX_LOGIN_ERROR_SUMMARY_LENGTH = 500
LOGIN_FAILURE_CATEGORIES = (
    (
        "browser_cleanup_failed",
        re.compile(r"(?i)(browser_cleanup_failed|browser_profile_in_use)"),
    ),
    (
        "otp_timeout",
        re.compile(
            r"(?is)(gotify|验证码|短信).{0,120}(timeout|超时)|"
            r"(timeout|超时).{0,120}(gotify|验证码|短信)"
        ),
    ),
    (
        "login_page_timeout",
        re.compile(
            r"(?is)(loginName|登录页|登录框).{0,120}(timeout|超时|未找到)|"
            r"(timeout|超时|未找到).{0,120}(loginName|登录页|登录框)"
        ),
    ),
    (
        "browser_error",
        re.compile(
            r"(?i)(webdriver|selenium|msedge|edge browser|driver.*启动|浏览器.*启动)"
        ),
    ),
    (
        "session_capture_failed",
        re.compile(
            r"(?is)(cookie|storage).{0,120}(写入失败|未就绪|capture.*fail|ready.*fail)"
        ),
    ),
)
LOGIN_FAILURE_SUMMARIES = {
    "browser_cleanup_failed": "自动登录浏览器未能安全关闭",
    "otp_timeout": "等待验证码超时",
    "login_page_timeout": "登录页加载或登录框检测超时",
    "browser_error": "Edge WebDriver 启动或响应异常",
    "session_capture_failed": "会话认证材料捕获或就绪检查失败",
}
SAFE_LOGIN_DIAGNOSTIC_PREFIX = "AUTO_NOTIFY_LOGIN_DIAGNOSTIC"
SAFE_LOGIN_DIAGNOSTIC_PHASES = frozenset(
    {
        "init_driver",
        "ngboss_login",
        "ngboss_main",
        "app_login",
        "usm_console",
        "app_session_capture",
        "session_validation",
    }
)
SAFE_LOGIN_DIAGNOSTIC_REASONS = frozenset(
    {
        "profile_in_use",
        "devtools_active_port",
        "browser_start_failed",
        "driver_unreachable",
        "webdriver_unclassified",
        "timeout",
        "unexpected_exception",
    }
)
SAFE_LOGIN_DIAGNOSTIC_PATTERN = re.compile(
    rf"(?m)^{SAFE_LOGIN_DIAGNOSTIC_PREFIX} "
    r"phase=(?P<phase>[a-z_]+) "
    r"exception=(?P<exception>[A-Za-z][A-Za-z0-9_]{0,79}) "
    r"reason=(?P<reason>[a-z_]+)$"
)


def classify_login_failure(error: object) -> str:
    text = " ".join(str(error).split())
    for category, pattern in LOGIN_FAILURE_CATEGORIES:
        if pattern.search(text):
            return category
    return "unknown"


def summarize_login_failure(error):
    category = classify_login_failure(error)
    return LOGIN_FAILURE_SUMMARIES.get(category, "unknown")[:MAX_LOGIN_ERROR_SUMMARY_LENGTH]


def extract_safe_login_diagnostic(error: object) -> str | None:
    """Return only a validated child-login marker, never raw child output."""
    match = SAFE_LOGIN_DIAGNOSTIC_PATTERN.search(str(error))
    if not match:
        return None
    values = match.groupdict()
    if (
        values["phase"] not in SAFE_LOGIN_DIAGNOSTIC_PHASES
        or values["reason"] not in SAFE_LOGIN_DIAGNOSTIC_REASONS
    ):
        return None
    return (
        f"阶段={values['phase']}，异常={values['exception']}，"
        f"原因={values['reason']}"
    )


class SessionLoginError(RuntimeError):
    def __init__(self, errors, *, preserve_diagnostics=False):
        self.errors = (
            [str(error) for error in errors]
            if preserve_diagnostics
            else [summarize_login_failure(error) for error in errors]
        )
        self.attempt_count = len(self.errors)
        super().__init__(f"自动登录累计失败 {self.attempt_count} 次")


def classify_probe_validation(probe_validation):
    if probe_validation and probe_validation.get("valid"):
        return PROBE_HEALTHY

    failures = [
        item
        for item in (probe_validation or {}).get("results", [])
        if not item.get("ok")
    ]
    if any(
        item.get("status_code") in AUTHENTICATION_STATUS_CODES
        or item.get("reason") in AUTHENTICATION_FAILURE_REASONS
        for item in failures
    ):
        return PROBE_AUTHENTICATION_FAILURE
    return PROBE_INFRASTRUCTURE_FAILURE


def format_probe_failure(item, *, include_diagnostics=False):
    stage = item.get("stage") or "unknown"
    reason = item.get("reason") or "unknown"
    parts = [f"{stage} 探活失败", f"原因={reason}"]
    if item.get("status_code") is not None:
        parts.append(f"HTTP={item.get('status_code')}")
    if include_diagnostics and item.get("error"):
        error_type = str(item["error"]).split(":", 1)[0].strip()
        if error_type:
            parts.append(f"错误类型={error_type}")
    return "，".join(parts)


def format_probe_validation_error(probe_validation, *, include_diagnostics=False):
    failures = [
        format_probe_failure(item, include_diagnostics=include_diagnostics)
        for item in (probe_validation or {}).get("results", [])
        if not item.get("ok")
    ]
    if not failures:
        return "session 探活失败，但未返回具体失败项"
    return "；".join(failures)


def sync_cookie_dump(source_path, target_path):
    source = Path(source_path).resolve()
    target = Path(target_path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"登录完成后仍未找到 Cookie 文件: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if source != target:
        shutil.copy2(source, target)
    return target


def publish_cookie_dump(source_path, target_path):
    source = Path(source_path).resolve()
    target = Path(target_path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"登录完成后仍未找到 Cookie 文件: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)
    return target


def merge_cookie_dump_stages(existing_cookie_dump, refreshed_cookie_dump):
    """Keep stages not included in a scoped login refresh."""
    existing_cookie_dump = existing_cookie_dump or {}
    refreshed_cookie_dump = refreshed_cookie_dump or {}
    existing_stages = existing_cookie_dump.get("stages") or []
    refreshed_stages = refreshed_cookie_dump.get("stages") or []
    if not isinstance(existing_stages, list) or not isinstance(refreshed_stages, list):
        return refreshed_cookie_dump

    refreshed_by_name = {
        str(stage.get("stage")): stage
        for stage in refreshed_stages
        if isinstance(stage, dict) and stage.get("stage")
    }
    merged_stages = []
    for stage in existing_stages:
        if not isinstance(stage, dict) or not stage.get("stage"):
            continue
        stage_name = str(stage["stage"])
        merged_stages.append(refreshed_by_name.pop(stage_name, stage))
    merged_stages.extend(refreshed_by_name.values())

    merged_cookie_dump = dict(refreshed_cookie_dump)
    merged_cookie_dump["stages"] = merged_stages
    return merged_cookie_dump


def run_login_command(
    command,
    cwd=PROJECT_DIR,
    timeout_seconds=None,
    env=None,
    preserve_diagnostics=False,
):
    process_env = os.environ.copy()
    process_env.update(env or {})
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            env=process_env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("登录命令执行失败，错误类别=unknown") from None
    if completed.returncode != 0:
        raw_diagnostic = completed.stderr or completed.stdout or ""
        if preserve_diagnostics:
            message = f"登录命令执行失败，退出码={completed.returncode}"
            if raw_diagnostic:
                message += f"，诊断={raw_diagnostic}"
            raise RuntimeError(message)
        category = classify_login_failure(raw_diagnostic)
        diagnostic = summarize_login_failure(raw_diagnostic)
        safe_child_diagnostic = extract_safe_login_diagnostic(raw_diagnostic)
        if safe_child_diagnostic and category != "unknown":
            diagnostic = f"{diagnostic}，{safe_child_diagnostic}"
        message = (
            f"登录命令执行失败，退出码={completed.returncode}，"
            f"错误类别={category}"
        )
        if category != "unknown" and diagnostic:
            message += f"，诊断={diagnostic}"
        if category == "browser_cleanup_failed":
            raise BrowserSessionCleanupError(message)
        raise RuntimeError(message)
    return {
        "returncode": completed.returncode,
    }


def format_cookie_validation(validation):
    """Format only the actionable, non-sensitive static Cookie validation fields."""
    validation = validation or {}
    return (
        f"missing_stages={validation.get('missing_stages') or []}，"
        f"expired_or_empty_stages={validation.get('expired_or_empty_stages') or []}，"
        f"too_old={bool(validation.get('too_old', False))}"
    )


def expand_login_command(command: str) -> str:
    """Resolve the configured Python placeholder for the active Worker."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("login_command 必须是非空字符串")
    python_executable = subprocess.list2cmdline([str(Path(sys.executable))])
    return command.replace("{python_executable}", python_executable)


def run_login_with_retry(
    login_attempt,
    *,
    max_attempts=2,
    retry_delay_seconds=0,
    sleeper=time.sleep,
    preserve_diagnostics=False,
):
    errors = []
    for attempt in range(1, max_attempts + 1):
        try:
            result = login_attempt()
            return {**result, "attempt_count": attempt}
        except BrowserSessionCleanupError as exc:
            raise SessionLoginError(
                [exc], preserve_diagnostics=preserve_diagnostics
            ) from exc
        except SessionInfrastructureError:
            raise
        except Exception as exc:
            errors.append(exc if preserve_diagnostics else summarize_login_failure(exc))
            if attempt == max_attempts:
                raise SessionLoginError(
                    errors, preserve_diagnostics=preserve_diagnostics
                ) from exc
            if retry_delay_seconds > 0:
                sleeper(retry_delay_seconds)
    raise AssertionError("unreachable")


def load_cookie_snapshot_if_exists(path):
    resolved = Path(path).resolve()
    try:
        raw_payload = resolved.read_bytes()
    except FileNotFoundError:
        return None, None
    cookie_hash = hashlib.sha256(raw_payload).hexdigest()
    payload = json.loads(raw_payload.decode("utf-8"))
    return payload, cookie_hash


def load_cookie_dump_if_exists(path):
    payload, _ = load_cookie_snapshot_if_exists(path)
    return payload


def validate_existing_session(cookie_dump, required_stages, stage_probes, min_ttl_seconds=0, max_age_seconds=None):
    validation = validate_cookie_dump(
        cookie_dump or {},
        required_stages,
        min_ttl_seconds=min_ttl_seconds,
        max_age_seconds=max_age_seconds,
    )
    if not validation["valid"]:
        return validation, None
    probe_validation = validate_stage_probes(cookie_dump or {}, required_stages, stage_probes)
    validation["probe_validation"] = probe_validation
    return validation, probe_validation


@contextmanager
def session_login_lock(lock_path, *, wait_seconds, poll_seconds, stale_seconds):
    acquired = False
    try:
        with file_lock(
            lock_path,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
            stale_seconds=stale_seconds,
        ) as lock_result:
            acquired = True
            yield lock_result
    except TimeoutError as exc:
        if acquired:
            raise
        raise SessionInfrastructureError(str(exc)) from exc


def prepare_session(
    config,
    base_dir=PROJECT_DIR,
    force_refresh=False,
    event_logger=None,
    login_attempts: int | None = None,
    preserve_login_diagnostics=False,
):
    cookie_dump_path = resolve_path(
        config.get("cookie_dump_path", "session/cookie_dump.json"),
        base_dir,
    )
    legacy_cookie_dump_path = resolve_path(config.get("legacy_cookie_dump_path"), base_dir)
    required_stages = config.get("required_stages") or []
    stage_probes = config.get("stage_probes") or {}
    min_ttl_seconds = int(config.get("min_ttl_seconds", 0) or 0)
    max_age_seconds = config.get("max_age_seconds")
    if max_age_seconds is not None:
        max_age_seconds = int(max_age_seconds)
    login_max_attempts = config.get("login_max_attempts", 2)
    login_retry_delay_seconds = config.get("login_retry_delay_seconds", 0)
    if login_max_attempts != 2:
        raise ValueError("login_max_attempts 必须固定为 2")
    if login_retry_delay_seconds != 0:
        raise ValueError("login_retry_delay_seconds 必须固定为 0")
    if login_attempts is None:
        effective_login_attempts = login_max_attempts
    elif type(login_attempts) is int and login_attempts in {1, 2}:
        effective_login_attempts = login_attempts
    else:
        raise ValueError("login_attempts 只支持 1 或 2")

    started_at = time.monotonic()

    def warn(message, *args):
        LOGGER.warning(message, *args)
        if event_logger:
            event_logger.warning(message, *args)

    def info(message, *args):
        LOGGER.info(message, *args)
        if event_logger:
            event_logger.info(message, *args)

    cookie_dump, _ = load_cookie_snapshot_if_exists(cookie_dump_path)
    if not cookie_dump and legacy_cookie_dump_path and legacy_cookie_dump_path.exists():
        sync_cookie_dump(legacy_cookie_dump_path, cookie_dump_path)
        cookie_dump, _ = load_cookie_snapshot_if_exists(cookie_dump_path)

    if cookie_dump and not force_refresh:
        validation = validate_cookie_dump(cookie_dump, required_stages, min_ttl_seconds=min_ttl_seconds, max_age_seconds=max_age_seconds)
        if validation["valid"]:
            probe_started_at = time.monotonic()
            probe_validation = validate_stage_probes(cookie_dump, required_stages, stage_probes)
            info(
                "会话探活完成: valid=%s elapsed_seconds=%.2f",
                bool(probe_validation.get("valid")),
                time.monotonic() - probe_started_at,
            )
            validation["probe_validation"] = probe_validation
            if probe_validation["valid"]:
                info(
                    "会话准备完成: source=stage_probe elapsed_seconds=%.2f",
                    time.monotonic() - started_at,
                )
                return {
                    "status": "reused",
                    "cookie_dump_path": str(cookie_dump_path),
                    "validation": validation,
                }
            failure_kind = classify_probe_validation(probe_validation)
            if failure_kind == PROBE_INFRASTRUCTURE_FAILURE:
                raise SessionInfrastructureError(
                    format_probe_validation_error(
                        probe_validation,
                        include_diagnostics=True,
                    )
                )
            warn(
                "共享会话已失效，进入登录锁后执行一次刷新: %s",
                format_probe_validation_error(probe_validation),
            )
        else:
            warn(
                "已有 Cookie 静态检查失败，将执行一次刷新: %s",
                format_cookie_validation(validation),
            )
    else:
        validation = {
            "valid": False,
            "available_stages": [],
            "missing_stages": required_stages,
            "expired_or_empty_stages": [],
        }

    if config.get("allow_login", True) is False:
        return {
            "status": "invalid",
            "cookie_dump_path": str(cookie_dump_path),
            "validation": validation,
            "reason": "login_disabled",
        }

    command = config.get("login_command")
    if not command:
        raise ValueError("Cookie 无效且未配置 login_command")
    command = expand_login_command(command)
    browser_session_state_path = resolve_path(
        config.get("browser_session_state_path", "session/browser-session.json"),
        base_dir,
    )
    browser_config = config.get("browser") or {}
    browser_user_data_dir = resolve_path(browser_config.get("user_data_dir"), base_dir)

    login_timeout_seconds = int(
        config.get("login_timeout_seconds", DEFAULT_LOGIN_TIMEOUT_SECONDS)
        or DEFAULT_LOGIN_TIMEOUT_SECONDS
    )
    lock_path = resolve_path(
        config.get("login_lock_path", "session/locks/login.lock"),
        base_dir,
    )
    lock_wait_seconds = int(config.get("login_lock_wait_seconds", DEFAULT_LOCK_WAIT_SECONDS) or DEFAULT_LOCK_WAIT_SECONDS)
    lock_poll_seconds = int(config.get("login_lock_poll_seconds", DEFAULT_LOCK_POLL_SECONDS) or DEFAULT_LOCK_POLL_SECONDS)
    lock_stale_seconds = int(config.get("login_lock_stale_seconds", DEFAULT_LOCK_STALE_SECONDS) or DEFAULT_LOCK_STALE_SECONDS)
    minimum_lock_wait_seconds = (
        login_max_attempts * login_timeout_seconds
        + login_retry_delay_seconds
        + LOGIN_ENVELOPE_OVERHEAD_SECONDS
    )
    if lock_wait_seconds <= minimum_lock_wait_seconds:
        raise ValueError(
            "login_lock_wait_seconds 必须大于完整登录重试包络 "
            f"({minimum_lock_wait_seconds} 秒)"
        )

    lock_wait_started_at = time.monotonic()
    with session_login_lock(
        lock_path,
        wait_seconds=lock_wait_seconds,
        poll_seconds=lock_poll_seconds,
        stale_seconds=lock_stale_seconds,
    ) as lock_result:
        info(
            "会话登录锁已获取: elapsed_seconds=%.2f",
            time.monotonic() - lock_wait_started_at,
        )
        # Re-check under the login lock. Parallel flow runs may have refreshed
        # cookies between this run's first probe/download failure and lock acquisition.
        locked_cookie_dump, _ = load_cookie_snapshot_if_exists(
            cookie_dump_path
        )
        locked_validation, locked_probe_validation = validate_existing_session(
            locked_cookie_dump,
            required_stages,
            stage_probes,
            min_ttl_seconds=min_ttl_seconds,
            max_age_seconds=max_age_seconds,
        )
        if locked_validation["valid"] and locked_probe_validation and locked_probe_validation["valid"]:
            info(
                "会话准备完成: source=lock_recheck elapsed_seconds=%.2f",
                time.monotonic() - started_at,
            )
            return {
                "status": "reused_after_lock",
                "cookie_dump_path": str(cookie_dump_path),
                "validation": locked_validation,
                "lock": lock_result,
            }
        if locked_validation["valid"]:
            failure_kind = classify_probe_validation(locked_probe_validation)
            if failure_kind == PROBE_INFRASTRUCTURE_FAILURE:
                raise SessionInfrastructureError(
                    format_probe_validation_error(
                        locked_probe_validation,
                        include_diagnostics=True,
                    )
                )
            warn(
                "登录锁内会话仍失效，将执行一次刷新: %s",
                format_probe_validation_error(locked_probe_validation),
            )
        else:
            warn(
                "登录锁内 Cookie 静态检查仍失败，将执行一次刷新: %s",
                format_cookie_validation(locked_validation),
            )

        attempt_number = 0

        def login_attempt():
            nonlocal attempt_number
            attempt_number += 1
            attempt_started_at = time.monotonic()
            info(
                "完整登录尝试开始: attempt=%s max_attempts=%s",
                attempt_number,
                effective_login_attempts,
            )
            attempt_snapshot_path = cookie_dump_path.with_name(
                f".{cookie_dump_path.name}.{uuid4().hex}.attempt"
            )
            browser_close_started_at = time.monotonic()
            close_result = close_browser_session(
                browser_session_state_path,
                user_data_dir=browser_user_data_dir,
                wait_seconds=float(config.get("browser_close_wait_seconds", 10) or 10),
            )
            info(
                "登录浏览器清理完成: attempt=%s elapsed_seconds=%.2f",
                attempt_number,
                time.monotonic() - browser_close_started_at,
            )
            close_summary = summarize_browser_close_result(close_result)
            if close_summary["stopped_count"]:
                warn(
                    "重新登录前已关闭旧自动登录浏览器: %s",
                    format_browser_close_result(close_result),
                )
            if close_summary["remaining_count"]:
                warn(
                    "旧自动登录浏览器仍有残留进程，继续尝试登录: %s",
                    format_browser_close_result(close_result),
                )
            if not browser_cleanup_succeeded(close_result):
                raise BrowserSessionCleanupError("browser_cleanup_failed")
            try:
                login_environment = {
                    "AUTO_NOTIFY_COOKIE_DUMP_PATH": str(attempt_snapshot_path),
                    "AUTO_NOTIFY_BROWSER_CLEANED": "1",
                }
                if required_stages:
                    login_environment["AUTO_NOTIFY_REQUIRED_STAGES"] = ",".join(
                        required_stages
                    )
                run_login_command(
                    command,
                    cwd=base_dir,
                    timeout_seconds=login_timeout_seconds,
                    env=login_environment,
                    preserve_diagnostics=preserve_login_diagnostics,
                )
                refreshed_cookie_dump, _ = (
                    load_cookie_snapshot_if_exists(attempt_snapshot_path)
                )
                refreshed_validation = validate_cookie_dump(
                    refreshed_cookie_dump or {},
                    required_stages,
                    min_ttl_seconds=min_ttl_seconds,
                )
                if not refreshed_validation["valid"]:
                    raise RuntimeError(
                        "登录后 Cookie 仍不可用: "
                        + format_cookie_validation(refreshed_validation)
                    )
                refreshed_probe_validation = validate_stage_probes(
                    refreshed_cookie_dump or {},
                    required_stages,
                    stage_probes,
                )
                if not refreshed_probe_validation["valid"]:
                    failure_kind = classify_probe_validation(
                        refreshed_probe_validation
                    )
                    if failure_kind == PROBE_INFRASTRUCTURE_FAILURE:
                        raise SessionInfrastructureError(
                            format_probe_validation_error(
                                refreshed_probe_validation,
                                include_diagnostics=True,
                            )
                        )
                    raise RuntimeError(
                        "登录后 session 探活仍不可用: "
                        + format_probe_validation_error(refreshed_probe_validation)
                    )
                refreshed_validation["probe_validation"] = refreshed_probe_validation
                merged_cookie_dump = merge_cookie_dump_stages(
                    locked_cookie_dump,
                    refreshed_cookie_dump or {},
                )
                write_json(attempt_snapshot_path, merged_cookie_dump)
                publish_cookie_dump(attempt_snapshot_path, cookie_dump_path)
                info(
                    "完整登录尝试成功: attempt=%s elapsed_seconds=%.2f",
                    attempt_number,
                    time.monotonic() - attempt_started_at,
                )
                return {
                    "close": close_result,
                    "validation": refreshed_validation,
                }
            except Exception as exc:
                warn(
                    "完整登录尝试失败: attempt=%s elapsed_seconds=%.2f error_type=%s",
                    attempt_number,
                    time.monotonic() - attempt_started_at,
                    type(exc).__name__,
                )
                raise
            finally:
                attempt_snapshot_path.unlink(missing_ok=True)

        login_result = run_login_with_retry(
            login_attempt,
            max_attempts=effective_login_attempts,
            retry_delay_seconds=login_retry_delay_seconds,
            preserve_diagnostics=preserve_login_diagnostics,
        )

    completed_login_attempts = login_result.get("attempt_count", attempt_number)
    info(
        "会话刷新完成: attempts=%s elapsed_seconds=%.2f",
        completed_login_attempts,
        time.monotonic() - started_at,
    )
    return {
        "status": "refreshed",
        "cookie_dump_path": str(cookie_dump_path),
        "validation": login_result["validation"],
        "login": {"returncode": 0},
        "close": login_result["close"],
        "login_attempt_count": completed_login_attempts,
        "lock": lock_result,
    }


def prepare_session_from_config(
    config_path,
    base_dir=PROJECT_DIR,
    force_refresh=False,
    login_attempts: int | None = None,
    preserve_login_diagnostics=False,
):
    from services.session_broker import StageSessionBroker

    config_path = Path(config_path)
    if not config_path.is_absolute():
        config_path = Path(base_dir) / config_path
    config_path = config_path.resolve()
    config, _ = load_json(config_path)
    return StageSessionBroker(base_dir=base_dir).ensure(
        config,
        force_refresh=force_refresh,
        login_attempts=login_attempts,
        preserve_login_diagnostics=preserve_login_diagnostics,
    )
