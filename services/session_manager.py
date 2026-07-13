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

from services.browser_session import close_browser_session
from services.json_excel_service import get_by_path
from services.method_service import (
    AuthenticationMaterialMissingError,
    build_cookie_jar,
    build_headers,
    resolve_storage_references,
)
from services.runtime_paths import resolve_runtime_path
from services.session_health_state import (
    read_session_health,
    session_health_is_fresh,
    write_session_health_atomic,
)


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
    return resolve_runtime_path(value, project_dir=Path(base_dir))


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
    timeout = int(probe.get("timeout_seconds", 8) or 8)
    kwargs = {
        "headers": build_headers(probe, stage),
        "params": resolve_storage_references(probe.get("params"), stage) or None,
        "timeout": timeout,
        "verify": bool(probe.get("verify_ssl", False)),
        "allow_redirects": bool(probe.get("allow_redirects", False)),
    }
    body_type = str(probe.get("body_type") or "").strip().lower()
    if body_type == "json":
        kwargs["json"] = resolve_storage_references(probe.get("data", {}), stage)
    elif body_type == "form":
        kwargs["data"] = resolve_storage_references(probe.get("data", {}), stage)
    elif body_type == "raw":
        kwargs["data"] = resolve_storage_references(probe.get("raw_body", ""), stage)
    elif body_type:
        raise ValueError(f"probe body_type 只支持 form/json/raw: {body_type}")
    return kwargs


def execute_stage_probe(stage_name, probe, stage):
    session = requests.Session()
    session.trust_env = bool(probe.get("trust_env", False))
    session.cookies.update(build_cookie_jar(stage, cookie_names=probe.get("cookie_names")))
    session.headers.update({"User-Agent": "session-probe/1.0"})
    method = str(probe.get("method") or "GET").upper()
    response = session.request(method, probe["url"], **build_probe_request_kwargs(probe, stage))
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


def validate_stage_probes(cookie_dump, required_stages=None, stage_probes=None):
    required_stages = required_stages or []
    stage_probes = stage_probes or {}
    results = []
    for stage_name in required_stages:
        probe = stage_probes.get(stage_name) or {}
        if not probe or probe.get("enabled", True) is False:
            results.append({"stage": stage_name, "enabled": False, "ok": True, "reason": "no_probe"})
            continue
        stage = find_stage(cookie_dump, stage_name)
        if not stage:
            results.append({"stage": stage_name, "enabled": True, "ok": False, "reason": "missing_stage"})
            continue
        try:
            results.append(execute_stage_probe(stage_name, probe, stage))
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
            results.append({"stage": stage_name, "enabled": True, "ok": False, "reason": "probe_error", "error": str(exc)})
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


LOGIN_ERROR_SENSITIVE_PATTERN = re.compile(
    r"(?i)(用户名|username|password|cookie|token|storage|authorization|secret|stdout|stderr)"
)
MAX_LOGIN_ERROR_SUMMARY_LENGTH = 500


def summarize_login_failure(error):
    summary = " ".join(str(error).split())
    if LOGIN_ERROR_SENSITIVE_PATTERN.search(summary):
        return "<redacted login failure detail>"
    return summary[:MAX_LOGIN_ERROR_SUMMARY_LENGTH]


class SessionLoginError(RuntimeError):
    def __init__(self, errors):
        self.errors = [summarize_login_failure(error) for error in errors]
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


def format_probe_failure(item):
    stage = item.get("stage") or "unknown"
    reason = item.get("reason") or "unknown"
    parts = [f"{stage} 探活失败", f"原因={reason}"]
    if item.get("status_code") is not None:
        parts.append(f"HTTP={item.get('status_code')}")
    if item.get("actual_value") is not None or item.get("expected_value") is not None:
        parts.append(f"实际值={item.get('actual_value')!r}")
        parts.append(f"期望值={item.get('expected_value')!r}")
    if item.get("error"):
        parts.append(f"错误={item.get('error')}")
    if item.get("url"):
        parts.append(f"URL={item.get('url')}")
    return "，".join(parts)


def format_probe_validation_error(probe_validation):
    failures = [
        format_probe_failure(item)
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


def run_login_command(command, cwd=PROJECT_DIR, timeout_seconds=None, env=None):
    process_env = os.environ.copy()
    process_env.update(env or {})
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
    if completed.returncode != 0:
        raise RuntimeError(f"登录命令执行失败，退出码={completed.returncode}")
    return {
        "command": command,
        "returncode": completed.returncode,
    }


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
    retry_delay_seconds=60,
    sleeper=time.sleep,
):
    errors = []
    for attempt in range(1, max_attempts + 1):
        try:
            result = login_attempt()
            return {**result, "attempt_count": attempt}
        except SessionInfrastructureError:
            raise
        except Exception as exc:
            errors.append(summarize_login_failure(exc))
            if attempt == max_attempts:
                raise SessionLoginError(errors) from exc
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


def prepare_session(config, base_dir=PROJECT_DIR, force_refresh=False, event_logger=None):
    cookie_dump_path = resolve_path(
        config.get("cookie_dump_path", "runtime/session/cookie_dump.json"),
        base_dir,
    )
    session_health_state_path = resolve_runtime_path(
        config.get("session_health_state_path", "runtime/session/session-health.json"),
        project_dir=Path(base_dir),
    )
    freshness_seconds = int(
        os.environ.get("AUTO_NOTIFY_SESSION_FRESHNESS_SECONDS")
        or config.get("session_freshness_seconds", 180)
    )
    legacy_cookie_dump_path = resolve_path(config.get("legacy_cookie_dump_path"), base_dir)
    required_stages = config.get("required_stages") or []
    stage_probes = config.get("stage_probes") or {}
    min_ttl_seconds = int(config.get("min_ttl_seconds", 0) or 0)
    max_age_seconds = config.get("max_age_seconds")
    if max_age_seconds is not None:
        max_age_seconds = int(max_age_seconds)
    login_max_attempts = config.get("login_max_attempts", 2)
    login_retry_delay_seconds = config.get("login_retry_delay_seconds", 60)
    if login_max_attempts != 2:
        raise ValueError("login_max_attempts 必须固定为 2")
    if login_retry_delay_seconds != 60:
        raise ValueError("login_retry_delay_seconds 必须固定为 60")

    def warn(message, *args):
        LOGGER.warning(message, *args)
        if event_logger:
            event_logger.warning(message, *args)

    def write_healthy_state(cookie_hash):
        return write_session_health_atomic(
            session_health_state_path,
            {
                "healthy": True,
                "verified_at": datetime.now(timezone.utc).astimezone().isoformat(),
                "cookie_hash": cookie_hash,
                "healthy_stages": sorted(required_stages),
            },
        )

    def write_authentication_failure_state(cookie_hash):
        return write_session_health_atomic(
            session_health_state_path,
            {
                "healthy": False,
                "verified_at": datetime.now(timezone.utc).astimezone().isoformat(),
                "cookie_hash": cookie_hash,
                "healthy_stages": [],
                "failure_classification": PROBE_AUTHENTICATION_FAILURE,
            },
        )

    cookie_dump, cookie_hash = load_cookie_snapshot_if_exists(cookie_dump_path)
    if not cookie_dump and legacy_cookie_dump_path and legacy_cookie_dump_path.exists():
        sync_cookie_dump(legacy_cookie_dump_path, cookie_dump_path)
        cookie_dump, cookie_hash = load_cookie_snapshot_if_exists(cookie_dump_path)

    if cookie_dump and not force_refresh:
        validation = validate_cookie_dump(cookie_dump, required_stages, min_ttl_seconds=min_ttl_seconds, max_age_seconds=max_age_seconds)
        if validation["valid"]:
            if session_health_is_fresh(
                read_session_health(session_health_state_path),
                cookie_hash=cookie_hash,
                required_stages=required_stages,
                freshness_seconds=freshness_seconds,
            ):
                return {
                    "status": "reused_fresh",
                    "cookie_dump_path": str(cookie_dump_path),
                    "validation": validation,
                }
            probe_validation = validate_stage_probes(cookie_dump, required_stages, stage_probes)
            validation["probe_validation"] = probe_validation
            if probe_validation["valid"]:
                write_healthy_state(cookie_hash)
                return {
                    "status": "reused",
                    "cookie_dump_path": str(cookie_dump_path),
                    "validation": validation,
                }
            failure_kind = classify_probe_validation(probe_validation)
            if failure_kind == PROBE_INFRASTRUCTURE_FAILURE:
                raise SessionInfrastructureError(format_probe_validation_error(probe_validation))
            write_authentication_failure_state(cookie_hash)
            warn("已有 Cookie 探活失败，将进入登录锁并在锁内复检: %s", format_probe_validation_error(probe_validation))
        else:
            warn("已有 Cookie 静态检查失败，将重新登录: %s", validation)
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
        config.get("browser_session_state_path", "runtime/session/browser-session.json"),
        base_dir,
    )
    browser_config = config.get("browser") or {}
    browser_user_data_dir = resolve_path(browser_config.get("user_data_dir"), base_dir)

    login_timeout_seconds = int(
        config.get("login_timeout_seconds", DEFAULT_LOGIN_TIMEOUT_SECONDS)
        or DEFAULT_LOGIN_TIMEOUT_SECONDS
    )
    lock_path = resolve_path(
        config.get("login_lock_path", "runtime/session/locks/login.lock"),
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

    with session_login_lock(
        lock_path,
        wait_seconds=lock_wait_seconds,
        poll_seconds=lock_poll_seconds,
        stale_seconds=lock_stale_seconds,
    ) as lock_result:
        # Re-check under the login lock. Parallel flow runs may have refreshed
        # cookies between this run's first probe/download failure and lock acquisition.
        locked_cookie_dump, locked_cookie_hash = load_cookie_snapshot_if_exists(
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
            write_healthy_state(locked_cookie_hash)
            return {
                "status": "reused_after_lock",
                "cookie_dump_path": str(cookie_dump_path),
                "validation": locked_validation,
                "lock": lock_result,
            }
        if locked_validation["valid"]:
            failure_kind = classify_probe_validation(locked_probe_validation)
            if failure_kind == PROBE_INFRASTRUCTURE_FAILURE:
                raise SessionInfrastructureError(format_probe_validation_error(locked_probe_validation))
            write_authentication_failure_state(locked_cookie_hash)
            warn("登录锁内 Cookie 探活仍失败，将自行重新登录: %s", format_probe_validation_error(locked_probe_validation))
        else:
            warn("登录锁内 Cookie 静态检查仍失败，将自行重新登录: %s", locked_validation)

        def login_attempt():
            attempt_snapshot_path = cookie_dump_path.with_name(
                f".{cookie_dump_path.name}.{uuid4().hex}.attempt"
            )
            close_result = close_browser_session(
                browser_session_state_path,
                user_data_dir=browser_user_data_dir,
                wait_seconds=float(config.get("browser_close_wait_seconds", 10) or 10),
            )
            if close_result.get("stopped_pids"):
                warn("重新登录前已关闭旧自动登录浏览器: %s", close_result)
            if close_result.get("remaining_pids"):
                warn("旧自动登录浏览器仍有残留进程，继续尝试登录: %s", close_result)
            try:
                command_result = run_login_command(
                    command,
                    cwd=base_dir,
                    timeout_seconds=login_timeout_seconds,
                    env={"AUTO_NOTIFY_COOKIE_DUMP_PATH": str(attempt_snapshot_path)},
                )
                refreshed_cookie_dump, refreshed_cookie_hash = (
                    load_cookie_snapshot_if_exists(attempt_snapshot_path)
                )
                refreshed_validation = validate_cookie_dump(
                    refreshed_cookie_dump or {},
                    required_stages,
                    min_ttl_seconds=min_ttl_seconds,
                )
                if not refreshed_validation["valid"]:
                    raise RuntimeError(f"登录后 Cookie 仍不可用: {refreshed_validation}")
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
                                refreshed_probe_validation
                            )
                        )
                    write_authentication_failure_state(refreshed_cookie_hash)
                    raise RuntimeError(
                        "登录后 session 探活仍不可用: "
                        + format_probe_validation_error(refreshed_probe_validation)
                    )
                refreshed_validation["probe_validation"] = refreshed_probe_validation
                publish_cookie_dump(attempt_snapshot_path, cookie_dump_path)
                write_healthy_state(refreshed_cookie_hash)
                return {
                    "command": command_result,
                    "close": close_result,
                    "validation": refreshed_validation,
                }
            finally:
                attempt_snapshot_path.unlink(missing_ok=True)

        login_result = run_login_with_retry(
            login_attempt,
            max_attempts=login_max_attempts,
            retry_delay_seconds=login_retry_delay_seconds,
        )

    return {
        "status": "refreshed",
        "cookie_dump_path": str(cookie_dump_path),
        "validation": login_result["validation"],
        "login": login_result["command"],
        "close": login_result["close"],
        "login_attempt_count": login_result["attempt_count"],
        "lock": lock_result,
    }


def prepare_session_from_config(config_path, base_dir=PROJECT_DIR, force_refresh=False):
    config_path = resolve_path(config_path, base_dir)
    config, _ = load_json(config_path)
    # The bundled module configs intentionally express runtime paths from the
    # project root, not from config/modules. Preserve the caller's base_dir so
    # direct helper use resolves Cookie files and login commands consistently.
    return prepare_session(config, base_dir=base_dir, force_refresh=force_refresh)
