# Prefect Session Keeper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Prefect-scheduled Session Keeper that maintains the shared Windows login session, refreshes immediately on authentication failure, avoids login on infrastructure failures, and alerts through the existing WeCom robot after two failed login attempts.

**Architecture:** Keep session validation and refresh policy in `services.session_manager`; expose it through Prefect tasks and a short `session_keeper_flow` scheduled every 15 minutes. Keeper and business flows share the existing Cookie files, retained Edge profile, and cross-process login lock. Incident notification is a separate service with persisted deduplication state so one outage produces one alert and one recovery message.

**Tech Stack:** Python 3.11, Prefect 3.7, requests, Playwright/Edge, Windows file locking, JSON runtime state, WeCom robot webhook, pytest.

## Global Constraints

- Deploy all components on one Windows machine under the same Windows user.
- Keep the Edge browser after login and reuse the existing profile and Cookie snapshot.
- Schedule Keeper at `00`, `15`, `30`, and `45` minutes in `Asia/Shanghai`.
- Treat HTTP `302`, `401`, `403`, missing authentication material, and explicit session-expired responses as authentication failures.
- Treat DNS, timeout, VPN, TLS, connection, and target-service availability errors as infrastructure failures; they must not start a login.
- On authentication failure, start login immediately; after the first failed login wait exactly 60 seconds and retry once.
- Stop after two login attempts and send one WeCom alert; send one recovery message after health returns.
- Business flows may trigger the same refresh path immediately, but each flow may force at most one refresh and retry only the failed business step once.
- Keeper, business flows, and manual runs must share one cross-process login lock and recheck the session after acquiring it.
- Never write credentials, Cookie values, Token values, Storage values, or webhook URLs to logs, state files, alerts, tests, or tracked configuration.
- Do not modify the session-lifetime experiment runner as part of this implementation.

---

### Task 1: Classify Session Probe Failures

**Files:**
- Modify: `services/session_manager.py`
- Modify: `tests/test_session_manager.py`

**Interfaces:**
- Consumes: existing `validate_stage_probes(cookie_dump, required_stages, stage_probes) -> dict` result.
- Produces: `classify_probe_validation(probe_validation: dict | None) -> str` returning `"healthy"`, `"authentication"`, or `"infrastructure"`.
- Produces: `SessionInfrastructureError(RuntimeError)` carrying the formatted probe failure text.

- [ ] **Step 1: Write failing classification tests**

Add imports and focused tests to `tests/test_session_manager.py`:

```python
from services.session_manager import (
    SessionInfrastructureError,
    classify_probe_validation,
)


def test_probe_classification_marks_redirect_as_authentication_failure():
    result = classify_probe_validation(
        {
            "valid": False,
            "results": [
                {
                    "stage": "report_analysis",
                    "ok": False,
                    "reason": "status_not_allowed",
                    "status_code": 302,
                }
            ],
        }
    )

    assert result == "authentication"


def test_probe_classification_marks_explicit_expiry_as_authentication_failure():
    result = classify_probe_validation(
        {
            "valid": False,
            "results": [
                {"stage": "city_ops", "ok": False, "reason": "session_expired"}
            ],
        }
    )

    assert result == "authentication"


def test_probe_classification_marks_request_exception_as_infrastructure_failure():
    result = classify_probe_validation(
        {
            "valid": False,
            "results": [
                {
                    "stage": "smart_ops",
                    "ok": False,
                    "reason": "probe_error",
                    "error": "ConnectTimeout",
                }
            ],
        }
    )

    assert result == "infrastructure"


def test_probe_classification_prioritizes_authentication_when_failures_are_mixed():
    result = classify_probe_validation(
        {
            "valid": False,
            "results": [
                {"stage": "report_analysis", "ok": False, "status_code": 401},
                {"stage": "smart_ops", "ok": False, "reason": "probe_error"},
            ],
        }
    )

    assert result == "authentication"
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
python -m pytest tests/test_session_manager.py -k "probe_classification" -v
```

Expected: collection fails because `classify_probe_validation` and `SessionInfrastructureError` do not exist.

- [ ] **Step 3: Implement the minimal classifier**

Add near the probe formatting helpers in `services/session_manager.py`:

```python
PROBE_HEALTHY = "healthy"
PROBE_AUTHENTICATION_FAILURE = "authentication"
PROBE_INFRASTRUCTURE_FAILURE = "infrastructure"
AUTHENTICATION_STATUS_CODES = {302, 401, 403}
AUTHENTICATION_FAILURE_REASONS = {"session_expired", "missing_stage"}


class SessionInfrastructureError(RuntimeError):
    pass


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
```

- [ ] **Step 4: Verify classifier tests pass**

Run:

```powershell
python -m pytest tests/test_session_manager.py -k "probe_classification" -v
```

Expected: all four classification tests pass.

- [ ] **Step 5: Commit the classification unit**

```powershell
git add services/session_manager.py tests/test_session_manager.py
git commit -m "feat: classify session probe failures"
```

---

### Task 2: Refresh Immediately on Authentication Failure and Retry Login Once

**Files:**
- Modify: `services/session_manager.py`
- Modify: `config/modules/autologin.json`
- Modify: `tests/test_session_manager.py`

**Interfaces:**
- Consumes: `classify_probe_validation(...)` from Task 1.
- Produces: `SessionLoginError(RuntimeError)` with `attempt_count: int` and redacted `errors: list[str]`.
- Produces: `run_login_with_retry(login_attempt: Callable[[], dict], *, max_attempts: int = 2, retry_delay_seconds: int = 60, sleeper=time.sleep) -> dict`, where one attempt includes browser cleanup, login, Cookie synchronization, static validation, and three-stage post-login probes.
- Preserves: `prepare_session(config, base_dir=PROJECT_DIR, force_refresh=False, event_logger=None) -> dict`.

- [ ] **Step 1: Add failing tests for infrastructure suppression and authentication refresh**

Add to `tests/test_session_manager.py`:

```python
def test_prepare_session_does_not_login_for_infrastructure_probe_failure(monkeypatch):
    work_dir = make_work_dir()
    try:
        cookie_dump_path = work_dir / "cookie_dump.json"
        write_json(cookie_dump_path, valid_city_ops_cookie_dump())
        monkeypatch.setattr(
            session_manager,
            "validate_stage_probes",
            lambda *_args, **_kwargs: {
                "valid": False,
                "results": [
                    {
                        "stage": "city_ops",
                        "ok": False,
                        "reason": "probe_error",
                        "error": "ConnectTimeout",
                    }
                ],
            },
        )
        login_calls = []
        monkeypatch.setattr(
            session_manager,
            "run_login_command",
            lambda *args, **kwargs: login_calls.append(args) or {},
        )

        with pytest.raises(SessionInfrastructureError, match="city_ops"):
            prepare_session(session_config(cookie_dump_path))

        assert login_calls == []
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_prepare_session_authentication_failure_logs_in_immediately(monkeypatch):
    work_dir = make_work_dir()
    try:
        cookie_dump_path = work_dir / "cookie_dump.json"
        write_json(cookie_dump_path, valid_city_ops_cookie_dump())
        probe_results = iter(
            [
                {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]},
                {"valid": False, "results": [{"stage": "city_ops", "ok": False, "status_code": 401}]},
                {"valid": True, "results": [{"stage": "city_ops", "ok": True}]},
            ]
        )
        monkeypatch.setattr(session_manager, "validate_stage_probes", lambda *_args, **_kwargs: next(probe_results))
        login_calls = []
        monkeypatch.setattr(session_manager, "run_login_command", lambda *args, **kwargs: login_calls.append(args) or {"returncode": 0})
        monkeypatch.setattr(session_manager, "close_browser_session", lambda *_args, **_kwargs: {"stopped_pids": []})
        monkeypatch.setattr(session_manager, "sync_cookie_dump", lambda *_args, **_kwargs: cookie_dump_path)

        result = prepare_session(session_config(cookie_dump_path))

        assert result["status"] == "refreshed"
        assert len(login_calls) == 1
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
```

Add these exact helpers near the existing helpers in `tests/test_session_manager.py`:

```python
def valid_city_ops_cookie_dump():
    return {
        "stages": [
            {
                "stage": "city_ops",
                "cookies": [{"name": "JSESSIONID", "value": "sid", "domain": "example.com"}],
                "session_storage": {"uapToken": "token"},
            }
        ]
    }


def session_config(cookie_dump_path):
    return {
        "cookie_dump_path": str(cookie_dump_path),
        "required_stages": ["city_ops"],
        "login_command": "fake-login",
        "login_max_attempts": 2,
        "login_retry_delay_seconds": 60,
        "stage_probes": {"city_ops": {"method": "POST", "url": "https://example/getUserInfo"}},
    }
```

- [ ] **Step 2: Add failing tests for the exact retry limit and delay**

```python
def test_login_failure_waits_sixty_seconds_and_retries_once(monkeypatch):
    calls = []
    sleeps = []
    outcomes = iter([RuntimeError("first failure"), {"returncode": 0}])

    def login_attempt():
        calls.append("login")
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    result = session_manager.run_login_with_retry(
        login_attempt,
        max_attempts=2,
        retry_delay_seconds=60,
        sleeper=sleeps.append,
    )

    assert calls == ["login", "login"]
    assert sleeps == [60]
    assert result["attempt_count"] == 2


def test_login_stops_after_two_failed_attempts(monkeypatch):
    calls = []
    sleeps = []

    def login_attempt():
        calls.append("login")
        raise RuntimeError("login failed")

    with pytest.raises(session_manager.SessionLoginError) as exc_info:
        session_manager.run_login_with_retry(
            login_attempt,
            max_attempts=2,
            retry_delay_seconds=60,
            sleeper=sleeps.append,
        )

    assert calls == ["login", "login"]
    assert sleeps == [60]
    assert exc_info.value.attempt_count == 2
```

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```powershell
python -m pytest tests/test_session_manager.py -k "infrastructure_probe_failure or authentication_failure_logs_in or login_failure_waits or login_stops" -v
```

Expected: failures because the classifier is not wired into `prepare_session` and retry helpers do not exist.

- [ ] **Step 4: Implement login retry primitives**

Add to `services/session_manager.py`:

```python
class SessionLoginError(RuntimeError):
    def __init__(self, errors):
        self.errors = [str(error) for error in errors]
        self.attempt_count = len(self.errors)
        super().__init__(f"自动登录累计失败 {self.attempt_count} 次")


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
        except Exception as exc:
            errors.append(exc)
            if attempt == max_attempts:
                raise SessionLoginError(errors) from exc
            sleeper(retry_delay_seconds)
    raise AssertionError("unreachable")
```

Read `login_max_attempts` and `login_retry_delay_seconds` from `config` in `prepare_session`. Default them to `2` and `60`, and raise `ValueError` unless they are exactly `2` and `60`; these values are an approved production invariant, not a per-Flow tuning parameter.

- [ ] **Step 5: Wire classification and retry into `prepare_session`**

Before entering the login lock, classify a failed probe:

```python
failure_kind = classify_probe_validation(probe_validation)
if failure_kind == PROBE_INFRASTRUCTURE_FAILURE:
    raise SessionInfrastructureError(format_probe_validation_error(probe_validation))
```

Repeat the same classification after the lock recheck. Replace the direct login block with a nested `login_attempt()` that performs these exact actions in order:

```python
def login_attempt():
    close_result = close_browser_session(
        browser_session_state_path,
        user_data_dir=browser_user_data_dir,
        wait_seconds=float(config.get("browser_close_wait_seconds", 10) or 10),
    )
    command_result = run_login_command(
        command,
        cwd=base_dir,
        timeout_seconds=login_timeout_seconds,
    )
    source_path = legacy_cookie_dump_path or cookie_dump_path
    sync_cookie_dump(source_path, cookie_dump_path)
    refreshed_cookie_dump = load_cookie_dump_if_exists(cookie_dump_path)
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
        raise RuntimeError(
            "登录后 session 探活仍不可用: "
            + format_probe_validation_error(refreshed_probe_validation)
        )
    refreshed_validation["probe_validation"] = refreshed_probe_validation
    return {
        "command": command_result,
        "close": close_result,
        "validation": refreshed_validation,
    }
```

Pass this callback to `run_login_with_retry(...)`. A command failure, Cookie validation failure, or post-login probe failure counts as one complete failed attempt. The second attempt must close any browser left by the first attempt before starting again.

Do not publish `status="refreshed"` until the post-login probe is valid. Keep the current atomic Cookie synchronization behavior.

- [ ] **Step 6: Add production retry configuration**

Add to `config/modules/autologin.json`:

```json
{
  "login_max_attempts": 2,
  "login_retry_delay_seconds": 60
}
```

Merge these keys into the existing top-level object; do not replace existing configuration and do not add secrets.

- [ ] **Step 7: Verify session manager behavior**

Run:

```powershell
python -m pytest tests/test_session_manager.py -v
```

Expected: all session manager tests pass, including lock recheck, infrastructure suppression, immediate authentication refresh, 60-second delay, and two-attempt stop.

- [ ] **Step 8: Commit session refresh policy**

```powershell
git add services/session_manager.py config/modules/autologin.json tests/test_session_manager.py
git commit -m "feat: add bounded session login recovery"
```

---

### Task 3: Add Deduplicated WeCom Session Alerts

**Files:**
- Create: `services/session_alert_service.py`
- Create: `tests/test_session_alert_service.py`
- Create: `config/modules/session_keeper.json`

**Interfaces:**
- Consumes: `infrastructure.wecom_client.send_text(webhook_url, text, timeout=30)`.
- Produces: `notify_session_failure(config: dict, incident: dict, *, base_dir: Path = PROJECT_DIR, sender=send_text) -> dict`.
- Produces: `notify_session_recovery(config: dict, recovery: dict, *, base_dir: Path = PROJECT_DIR, sender=send_text) -> dict`.
- Persists: `runtime/session_keeper/incident_state.json` containing only incident key, timestamps, status, and non-secret metadata.

- [ ] **Step 1: Write failing alert formatting and deduplication tests**

Create `tests/test_session_alert_service.py`:

```python
from pathlib import Path

from services.session_alert_service import (
    notify_session_failure,
    notify_session_recovery,
)


def alert_config(tmp_path: Path):
    return {
        "webhook_url": "https://example.invalid/webhook",
        "incident_state_path": str(tmp_path / "incident_state.json"),
        "host_name": "windows-worker-1",
    }


def test_failure_alert_is_sent_once_for_same_incident(tmp_path):
    messages = []
    config = alert_config(tmp_path)
    incident = {
        "incident_key": "authentication:city_ops",
        "trigger_source": "session-keeper",
        "failure_category": "authentication",
        "failed_stages": ["city_ops"],
        "attempt_count": 2,
        "errors": ["first failure", "second failure"],
        "flow_run_id": "flow-123",
        "next_scheduled_at": "2026-07-12T16:30:00+08:00",
    }

    first = notify_session_failure(config, incident, sender=lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0})
    second = notify_session_failure(config, incident, sender=lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0})

    assert first["sent"] is True
    assert second["suppressed"] is True
    assert len(messages) == 1
    assert "flow-123" in messages[0]


def test_alert_text_never_contains_authentication_secrets(tmp_path):
    messages = []
    config = alert_config(tmp_path)
    incident = {
        "incident_key": "authentication:report_analysis",
        "trigger_source": "business-flow",
        "failure_category": "authentication",
        "failed_stages": ["report_analysis"],
        "attempt_count": 2,
        "errors": ["Cookie=raw-cookie Token=raw-token password=raw-password"],
        "flow_run_id": "flow-456",
        "next_scheduled_at": "2026-07-12T16:45:00+08:00",
    }

    notify_session_failure(config, incident, sender=lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0})

    assert "raw-cookie" not in messages[0]
    assert "raw-token" not in messages[0]
    assert "raw-password" not in messages[0]


def test_recovery_message_is_sent_once_and_clears_active_incident(tmp_path):
    messages = []
    config = alert_config(tmp_path)
    incident = {
        "incident_key": "authentication:city_ops",
        "trigger_source": "session-keeper",
        "failure_category": "authentication",
        "failed_stages": ["city_ops"],
        "attempt_count": 2,
        "errors": ["failure"],
        "flow_run_id": "flow-123",
        "next_scheduled_at": "2026-07-12T16:30:00+08:00",
    }
    sender = lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0}
    notify_session_failure(config, incident, sender=sender)

    first = notify_session_recovery(
        config,
        {"incident_key": incident["incident_key"], "flow_run_id": "flow-789"},
        sender=sender,
    )
    second = notify_session_recovery(
        config,
        {"incident_key": incident["incident_key"], "flow_run_id": "flow-790"},
        sender=sender,
    )

    assert first["sent"] is True
    assert second["suppressed"] is True
    assert len(messages) == 2
```

- [ ] **Step 2: Run alert tests and verify RED**

Run:

```powershell
python -m pytest tests/test_session_alert_service.py -v
```

Expected: import failure because `services.session_alert_service` does not exist.

- [ ] **Step 3: Implement the alert service**

Create `services/session_alert_service.py` with:

```python
from __future__ import annotations

import json
import re
import socket
from datetime import datetime
from pathlib import Path

from infrastructure.wecom_client import send_text


PROJECT_DIR = Path(__file__).resolve().parents[1]
SECRET_PATTERN = re.compile(
    r"(?i)(cookie|token|password|webhook)(\s*[=:]\s*)\S+"
)


def _resolve(path_value, base_dir):
    path = Path(path_value)
    return path if path.is_absolute() else base_dir / path


def _read_state(path):
    if not path.exists():
        return {"active_incident_key": None}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _redact(value):
    return SECRET_PATTERN.sub(r"\1\2<redacted>", str(value))


def notify_session_failure(config, incident, *, base_dir=PROJECT_DIR, sender=send_text):
    state_path = _resolve(config["incident_state_path"], base_dir)
    state = _read_state(state_path)
    if state.get("active_incident_key") == incident["incident_key"]:
        return {"sent": False, "suppressed": True}

    host_name = config.get("host_name") or socket.gethostname()
    errors = "；".join(_redact(item) for item in incident.get("errors", []))
    message = (
        f"[自动登录告警]\n主机: {host_name}\n"
        f"来源: {incident['trigger_source']}\n"
        f"分类: {incident['failure_category']}\n"
        f"阶段: {', '.join(incident.get('failed_stages', []))}\n"
        f"登录尝试: {incident.get('attempt_count', 0)}\n"
        f"错误: {errors}\n"
        f"Flow Run ID: {incident.get('flow_run_id')}\n"
        f"下次调度: {incident.get('next_scheduled_at')}"
    )
    result = sender(config["webhook_url"], message, timeout=30)
    _write_state(
        state_path,
        {
            "active_incident_key": incident["incident_key"],
            "failed_at": datetime.now().astimezone().isoformat(),
            "failure_category": incident["failure_category"],
        },
    )
    return {"sent": True, "suppressed": False, "result": result}


def notify_session_recovery(config, recovery, *, base_dir=PROJECT_DIR, sender=send_text):
    state_path = _resolve(config["incident_state_path"], base_dir)
    state = _read_state(state_path)
    if state.get("active_incident_key") != recovery["incident_key"]:
        return {"sent": False, "suppressed": True}
    message = (
        f"[自动登录恢复]\n故障: {recovery['incident_key']}\n"
        f"Flow Run ID: {recovery.get('flow_run_id')}"
    )
    result = sender(config["webhook_url"], message, timeout=30)
    _write_state(
        state_path,
        {
            "active_incident_key": None,
            "recovered_at": datetime.now().astimezone().isoformat(),
        },
    )
    return {"sent": True, "suppressed": False, "result": result}
```

The implementation is accepted only when the exact secret-redaction test above passes and neither rendered text nor persisted state contains the sentinel authentication values.

- [ ] **Step 4: Add Keeper configuration without secrets**

Create `config/modules/session_keeper.json`:

```json
{
  "autologin_config_path": "config/modules/autologin.json",
  "alert_config_path": "config/modules/wecom_sender.json",
  "incident_state_path": "runtime/session_keeper/incident_state.json",
  "login_max_attempts": 2,
  "login_retry_delay_seconds": 60,
  "required_stages": [
    "report_analysis",
    "smart_ops",
    "city_ops"
  ]
}
```

The implementation must load `config/modules/wecom_sender.local.json` through the repository's local-override loader. Do not copy the webhook into this new tracked file.

- [ ] **Step 5: Verify alert tests pass**

Run:

```powershell
python -m pytest tests/test_session_alert_service.py -v
```

Expected: all alert, deduplication, recovery, and secret-redaction tests pass.

- [ ] **Step 6: Commit alerting support**

```powershell
git add services/session_alert_service.py tests/test_session_alert_service.py config/modules/session_keeper.json
git commit -m "feat: add session incident alerts"
```

---

### Task 4: Add Prefect Session Keeper Flow

**Files:**
- Modify: `tasks/session_tasks.py`
- Create: `flows/session_keeper_flow.py`
- Create: `tests/test_session_keeper_flow.py`

**Interfaces:**
- Consumes: `prepare_session(config, force_refresh=False, event_logger=None)`.
- Consumes: `notify_session_failure(...)` and `notify_session_recovery(...)` from Task 3.
- Produces: `prepare_session_task(config: dict, force_refresh: bool = False) -> dict` with call-site configurable Prefect retries.
- Produces: `session_keeper_flow(config_path: str = "config/modules/session_keeper.json") -> dict`.

- [ ] **Step 1: Write failing Keeper Flow tests using direct function mocks**

Create `tests/test_session_keeper_flow.py`:

```python
import pytest

from flows import session_keeper_flow as keeper_module
from services.session_manager import SessionInfrastructureError, SessionLoginError


def test_keeper_reuses_healthy_session_and_sends_recovery(monkeypatch):
    recovery_calls = []
    monkeypatch.setattr(keeper_module, "load_keeper_config", lambda *_args, **_kwargs: ({"session": {}, "alert": {}}, None))
    monkeypatch.setattr(keeper_module, "run_prepare_session", lambda *_args, **_kwargs: {"status": "reused"})
    monkeypatch.setattr(keeper_module, "notify_recovery", lambda *_args, **_kwargs: recovery_calls.append(True) or {"sent": False})

    result = keeper_module.run_session_keeper("config/modules/session_keeper.json")

    assert result["status"] == "reused"
    assert recovery_calls == [True]


def test_keeper_alerts_after_two_login_attempts_fail(monkeypatch):
    alert_calls = []
    monkeypatch.setattr(keeper_module, "load_keeper_config", lambda *_args, **_kwargs: ({"session": {}, "alert": {}}, None))
    monkeypatch.setattr(
        keeper_module,
        "run_prepare_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SessionLoginError([RuntimeError("one"), RuntimeError("two")])),
    )
    monkeypatch.setattr(keeper_module, "notify_failure", lambda *_args, **_kwargs: alert_calls.append(True) or {"sent": True})

    with pytest.raises(SessionLoginError):
        keeper_module.run_session_keeper("config/modules/session_keeper.json")

    assert alert_calls == [True]


def test_keeper_infrastructure_failure_never_requests_login(monkeypatch):
    alert_calls = []
    monkeypatch.setattr(keeper_module, "load_keeper_config", lambda *_args, **_kwargs: ({"session": {}, "alert": {}}, None))
    monkeypatch.setattr(
        keeper_module,
        "run_prepare_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SessionInfrastructureError("ConnectTimeout")),
    )
    monkeypatch.setattr(keeper_module, "notify_failure", lambda *_args, **_kwargs: alert_calls.append(True) or {"sent": True})

    with pytest.raises(SessionInfrastructureError):
        keeper_module.run_session_keeper("config/modules/session_keeper.json")

    assert alert_calls == [True]
```

- [ ] **Step 2: Run Keeper tests and verify RED**

Run:

```powershell
python -m pytest tests/test_session_keeper_flow.py -v
```

Expected: import failure because `flows.session_keeper_flow` does not exist.

- [ ] **Step 3: Keep the Prefect task thin**

Retain `prepare_session_task` as the shared wrapper in `tasks/session_tasks.py`:

```python
@task
def prepare_session_task(config, force_refresh=False):
    return prepare_session(
        config,
        force_refresh=force_refresh,
        event_logger=get_run_logger(),
    )
```

Do not embed alerting, retries, or Keeper-specific policy in the task. Keeper uses `.with_options(retries=1, retry_delay_seconds=60)` so infrastructure exceptions receive one Prefect retry. Authentication login retry remains internal to Session Manager and is not duplicated by Prefect.

- [ ] **Step 4: Implement the Keeper Flow and testable core**

Create `flows/session_keeper_flow.py` with a plain `run_session_keeper()` core and a Prefect wrapper:

```python
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from prefect import flow, get_run_logger
from prefect.runtime import flow_run

from services.session_alert_service import notify_session_failure, notify_session_recovery
from services.session_manager import SessionInfrastructureError, SessionLoginError
from tasks.session_tasks import prepare_session_task
from utils.config_loader import load_json_with_local_override


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_keeper_config(config_path, base_dir=PROJECT_ROOT):
    keeper_config, resolved = load_json_with_local_override(base_dir / config_path)
    session_config, _ = load_json_with_local_override(base_dir / keeper_config["autologin_config_path"])
    alert_config, _ = load_json_with_local_override(base_dir / keeper_config["alert_config_path"])
    session_config["required_stages"] = keeper_config["required_stages"]
    session_config["login_max_attempts"] = keeper_config["login_max_attempts"]
    session_config["login_retry_delay_seconds"] = keeper_config["login_retry_delay_seconds"]
    return {
        "session": session_config,
        "alert": {
            "webhook_url": alert_config["wecom"]["webhook_url"],
            "incident_state_path": keeper_config["incident_state_path"],
        },
    }, resolved


def next_quarter_hour(now):
    minute = ((now.minute // 15) + 1) * 15
    if minute == 60:
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return now.replace(minute=minute, second=0, microsecond=0)


def current_flow_run_id():
    return str(flow_run.id or "manual")


def run_prepare_session(config):
    return prepare_session_task.with_options(
        retries=1,
        retry_delay_seconds=60,
    )(config, force_refresh=False)


def notify_failure(alert_config, incident):
    return notify_session_failure(alert_config, incident)


def notify_recovery(alert_config, recovery):
    return notify_session_recovery(alert_config, recovery)


def run_session_keeper(config_path="config/modules/session_keeper.json"):
    config, _ = load_keeper_config(config_path)
    run_id = current_flow_run_id()
    now = datetime.now().astimezone()
    try:
        result = run_prepare_session(config["session"])
    except SessionLoginError as exc:
        notify_failure(
            config["alert"],
            {
                "incident_key": "authentication:shared-session",
                "trigger_source": "session-keeper",
                "failure_category": "authentication",
                "failed_stages": config["session"]["required_stages"],
                "attempt_count": exc.attempt_count,
                "errors": exc.errors,
                "flow_run_id": run_id,
                "next_scheduled_at": next_quarter_hour(now).isoformat(),
            },
        )
        raise
    except SessionInfrastructureError as exc:
        notify_failure(
            config["alert"],
            {
                "incident_key": "infrastructure:shared-session",
                "trigger_source": "session-keeper",
                "failure_category": "infrastructure",
                "failed_stages": config["session"]["required_stages"],
                "attempt_count": 0,
                "errors": [str(exc)],
                "flow_run_id": run_id,
                "next_scheduled_at": next_quarter_hour(now).isoformat(),
            },
        )
        raise
    notify_recovery(
        config["alert"],
        {
            "incident_key": "authentication:shared-session",
            "flow_run_id": run_id,
        },
    )
    notify_recovery(
        config["alert"],
        {
            "incident_key": "infrastructure:shared-session",
            "flow_run_id": run_id,
        },
    )
    return result


@flow(name="session-keeper-flow")
def session_keeper_flow(config_path="config/modules/session_keeper.json"):
    get_run_logger().info("检查共享登录会话")
    return run_session_keeper(config_path)
```

In each direct unit test, monkeypatch `current_flow_run_id` to return a fixed value such as `"flow-test"`; this keeps Prefect runtime access outside the pure test path.

- [ ] **Step 5: Verify Keeper Flow tests pass**

Run:

```powershell
python -m pytest tests/test_session_keeper_flow.py -v
```

Expected: healthy, authentication failure, and infrastructure failure tests pass.

- [ ] **Step 6: Commit Keeper Flow**

```powershell
git add tasks/session_tasks.py flows/session_keeper_flow.py tests/test_session_keeper_flow.py
git commit -m "feat: add Prefect session keeper flow"
```

---

### Task 5: Preserve Immediate Refresh in Business Flows

**Files:**
- Create: `services/session_retry_service.py`
- Create: `tests/test_session_retry_service.py`
- Modify: `flows/notify_single_flow.py`
- Modify: `flows/dashboard_metric_flow.py`
- Create: `tests/test_dashboard_metric_flow_session.py`

**Interfaces:**
- Consumes: `prepare_session_task(config, force_refresh=True)`.
- Produces: `is_session_expired_error(exc: Exception) -> bool` in `services.session_retry_service`.
- Produces: `run_with_session_refresh_once(operation: Callable[[], T], refresh_session: Callable[[], dict]) -> T` in `services.session_retry_service`.
- Preserves: one forced refresh and one retry of only the failed operation.
- Keeps business Flow modules independent from one another.

- [ ] **Step 1: Add a focused test for one refresh and one business retry**

Create `tests/test_session_retry_service.py`:

```python
import pytest

from services.session_retry_service import run_with_session_refresh_once


def test_business_step_refreshes_once_and_retries_only_failed_step():
    calls = []

    def operation():
        calls.append("operation")
        if calls.count("operation") == 1:
            raise RuntimeError("session expired: HTTP 302")
        return "ok"

    def refresh():
        calls.append("refresh")
        return {"status": "refreshed"}

    assert run_with_session_refresh_once(operation, refresh) == "ok"
    assert calls == ["operation", "refresh", "operation"]


def test_business_step_does_not_refresh_for_network_failure():
    refresh_calls = []

    with pytest.raises(RuntimeError, match="ConnectTimeout"):
        run_with_session_refresh_once(
            lambda: (_ for _ in ()).throw(RuntimeError("ConnectTimeout")),
            lambda: refresh_calls.append(True),
        )

    assert refresh_calls == []


def test_business_step_stops_after_refresh_and_second_auth_failure():
    refresh_calls = []

    with pytest.raises(RuntimeError, match="HTTP 302"):
        run_with_session_refresh_once(
            lambda: (_ for _ in ()).throw(RuntimeError("session expired: HTTP 302")),
            lambda: refresh_calls.append(True) or {"status": "refreshed"},
        )

    assert refresh_calls == [True]
```

- [ ] **Step 2: Run business-flow tests and verify RED**

Run:

```powershell
python -m pytest tests/test_session_retry_service.py -v
```

Expected: import failure because `services.session_retry_service` does not exist.

- [ ] **Step 3: Implement the shared authentication error and one-refresh helpers**

Create `services/session_retry_service.py`:

```python
from __future__ import annotations


SESSION_EXPIRED_MARKERS = (
    "session expired",
    "session_expired",
    "http 302",
    "http 401",
    "http 403",
    "登录超时",
    "请重新登录",
)


def is_session_expired_error(exc):
    message = str(exc or "").lower()
    return any(marker in message for marker in SESSION_EXPIRED_MARKERS)


def run_with_session_refresh_once(operation, refresh_session):
    try:
        return operation()
    except RuntimeError as exc:
        if not is_session_expired_error(exc):
            raise
        refresh_result = refresh_session()
        if refresh_result.get("status") == "invalid":
            raise RuntimeError(f"重新登录失败: {refresh_result.get('reason')}") from exc
        return operation()
```

Modify `flows/notify_single_flow.py` to import both helpers from `services.session_retry_service`. Remove the local `is_session_expired_error` definition and replace the local download retry closure with `run_with_session_refresh_once(...)`. Only the failed download step runs again; Excel generation, screenshots, and previously completed steps remain outside the retry boundary.

- [ ] **Step 4: Add failing dashboard Flow refresh tests**

Create `tests/test_dashboard_metric_flow_session.py`:

```python
import pytest

from flows import dashboard_metric_flow as dashboard_flow


def test_dashboard_flow_retries_collection_once_with_force_refresh(monkeypatch):
    calls = []

    def fake_task(**kwargs):
        calls.append(kwargs["force_refresh"])
        if len(calls) == 1:
            raise RuntimeError("session expired: HTTP 302")
        return {"batch_no": "batch-1", "timing": {}}

    monkeypatch.setattr(dashboard_flow, "run_dashboard_metric_task", fake_task)

    result = dashboard_flow.run_dashboard_metric_with_session_refresh(
        mode="REALTIME",
        config_path="config/dashboard/session.json",
        trigger_type="SCHEDULED",
        force_refresh=False,
    )

    assert result["batch_no"] == "batch-1"
    assert calls == [False, True]


def test_dashboard_flow_does_not_refresh_for_network_failure(monkeypatch):
    calls = []

    def fake_task(**kwargs):
        calls.append(kwargs["force_refresh"])
        raise RuntimeError("ConnectTimeout")

    monkeypatch.setattr(dashboard_flow, "run_dashboard_metric_task", fake_task)

    with pytest.raises(RuntimeError, match="ConnectTimeout"):
        dashboard_flow.run_dashboard_metric_with_session_refresh(
            mode="REALTIME",
            config_path="config/dashboard/session.json",
            trigger_type="SCHEDULED",
            force_refresh=False,
        )

    assert calls == [False]
```

- [ ] **Step 5: Implement one dashboard task retry**

Add a testable helper to `flows/dashboard_metric_flow.py` and make `dashboard_metric_flow()` call it:

```python
from services.session_retry_service import is_session_expired_error


def run_dashboard_metric_with_session_refresh(
    *,
    mode,
    config_path,
    trigger_type,
    force_refresh,
):
    parameters = {
        "mode": mode,
        "config_path": config_path,
        "trigger_type": trigger_type,
        "force_refresh": force_refresh,
    }
    try:
        return run_dashboard_metric_task(**parameters)
    except RuntimeError as exc:
        if force_refresh or not is_session_expired_error(exc):
            raise
        return run_dashboard_metric_task(**{**parameters, "force_refresh": True})
```

The dashboard batch is the failed business step for this Flow. Its database writes are already finalized through the batch lifecycle, so one full batch retry is the atomic retry boundary.

- [ ] **Step 6: Run business integration tests**

Run:

```powershell
python -m pytest tests/test_session_retry_service.py tests/test_dashboard_metric_flow_session.py -v
```

Expected: all immediate-refresh and retry-limit tests pass.

- [ ] **Step 7: Commit business-flow integration**

```powershell
git add services/session_retry_service.py tests/test_session_retry_service.py flows/notify_single_flow.py flows/dashboard_metric_flow.py tests/test_dashboard_metric_flow_session.py
git commit -m "refactor: share bounded session refresh in business flows"
```

---

### Task 6: Deploy Keeper on Fixed Quarter-Hour Schedule and Trigger at Startup

**Files:**
- Modify: `prefect.yaml`
- Modify: `scripts/run.ps1`
- Modify: `tests/test_development_environment_contract.py`

**Interfaces:**
- Consumes: `flows/session_keeper_flow.py:session_keeper_flow`.
- Produces: Prefect Deployment `session-keeper-flow/session-keeper` on work pool `default-agent-pool`.
- Produces: one immediate queued Keeper run after Worker startup.

- [ ] **Step 1: Add failing deployment contract tests**

Add to `tests/test_development_environment_contract.py`:

```python
def test_prefect_deploys_session_keeper_on_fixed_quarter_hours():
    prefect_config = (ROOT / "prefect.yaml").read_text(encoding="utf-8")

    assert "name: session-keeper" in prefect_config
    assert "flows/session_keeper_flow.py:session_keeper_flow" in prefect_config
    assert 'cron: "*/15 * * * *"' in prefect_config
    assert "timezone: Asia/Shanghai" in prefect_config


def test_runtime_start_queues_initial_session_keeper_run_after_worker_start():
    source = (ROOT / "scripts" / "run.ps1").read_text(encoding="utf-8")

    worker_marker = 'Write-Host "启动 Prefect Worker..."'
    keeper_command = 'prefect deployment run "session-keeper-flow/session-keeper"'
    assert keeper_command in source
    assert source.index(worker_marker) < source.index(keeper_command)
```

- [ ] **Step 2: Run deployment tests and verify RED**

Run:

```powershell
python -m pytest tests/test_development_environment_contract.py -k "session_keeper" -v
```

Expected: both tests fail because the Deployment and startup trigger are absent.

- [ ] **Step 3: Add the Prefect Deployment**

Append to `prefect.yaml`:

```yaml
  - name: session-keeper
    entrypoint: flows/session_keeper_flow.py:session_keeper_flow
    parameters:
      config_path: config/modules/session_keeper.json
    schedules:
      - cron: "*/15 * * * *"
        timezone: Asia/Shanghai
        active: true
    work_pool:
      name: default-agent-pool
      work_queue_name: default
```

- [ ] **Step 4: Queue one Keeper run during stack startup**

In `scripts/run.ps1`, after the Prefect Worker start command and before the optional Web startup, add:

```powershell
Write-Host "触发首次 Session Keeper 检查..."
& $PythonExe -m prefect deployment run "session-keeper-flow/session-keeper"
if ($LASTEXITCODE -ne 0) {
    throw "首次 Session Keeper Flow 提交失败"
}
```

This command should queue the run and return; do not add `--watch`.

- [ ] **Step 5: Verify deployment contract tests pass**

Run:

```powershell
python -m pytest tests/test_development_environment_contract.py -k "session_keeper" -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit deployment and startup integration**

```powershell
git add prefect.yaml scripts/run.ps1 tests/test_development_environment_contract.py
git commit -m "feat: schedule session keeper deployment"
```

---

### Task 7: End-to-End Verification and Operations Documentation

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_GUIDE.md`
- Test: all files changed in Tasks 1-6

**Interfaces:**
- Consumes: completed Keeper, alert, business refresh, and Deployment behavior.
- Produces: operator instructions and verification evidence; no new runtime API.

- [ ] **Step 1: Document the operating model**

Add a concise `Session Keeper` section to `README.md` covering:

```text
- Windows-only deployment context and retained Edge requirement.
- Prefect Deployment name: session-keeper-flow/session-keeper.
- Fixed schedule: every 15 minutes at 00/15/30/45 Asia/Shanghai.
- Authentication failure: immediate login, 60-second delay, one retry.
- Infrastructure failure: no login; Prefect retry and WeCom alert.
- Business flows may force one refresh and retry one failed step.
- Incident state path and how to inspect the latest Prefect run.
- No credentials or authentication material may be copied into support logs.
```

Add the same lifecycle constraint to `PROJECT_GUIDE.md`: Keeper and business flows must use the shared Session Manager and global login lock; no new direct login entrypoint may bypass them.

- [ ] **Step 2: Run focused tests**

Run:

```powershell
python -m pytest tests/test_session_manager.py tests/test_session_alert_service.py tests/test_session_keeper_flow.py tests/test_session_retry_service.py tests/test_dashboard_metric_flow_session.py tests/test_dashboard_v2_trigger.py tests/test_development_environment_contract.py tests/test_notify_service.py -q
```

Expected: all focused tests pass with no test failures. Existing repository `.pytest_cache` permission warnings may be reported separately but do not count as behavior failures.

- [ ] **Step 3: Run the full test suite**

Run:

```powershell
python -m pytest -q
```

Expected: all tests pass. Investigate any failure before proceeding; do not waive failures as unrelated without evidence.

- [ ] **Step 4: Validate tracked configuration and deployment syntax**

Run:

```powershell
python -c "import json; json.load(open('config/modules/autologin.json', encoding='utf-8')); json.load(open('config/modules/session_keeper.json', encoding='utf-8')); print('json_ok')"
python -c "import yaml; yaml.safe_load(open('prefect.yaml', encoding='utf-8')); print('yaml_ok')"
```

Expected: `json_ok` and `yaml_ok`.

- [ ] **Step 5: Scan changed files for secrets and placeholders**

Run:

```powershell
rg -n "YOUR_KEY|raw-cookie|raw-token|raw-password|JSESSIONID=.*|accessToken=.*" services/session_alert_service.py flows/session_keeper_flow.py config/modules/session_keeper.json README.md PROJECT_GUIDE.md
git diff --check
```

Expected: no real credentials, Cookie values, Token values, webhook keys, placeholders, or whitespace errors in changed files. Test-only sentinel strings must remain confined to tests and must never be emitted by alert formatting.

- [ ] **Step 6: Perform a controlled dry operational check**

With a valid local override and Prefect development environment running:

```powershell
python -m prefect deployment run "session-keeper-flow/session-keeper" --watch
```

Expected for a healthy session: Flow completes with `status=reused` or `status=reused_after_lock`, no login command runs, no WeCom failure alert is sent, and the retained Edge remains alive.

Then simulate an infrastructure failure using a test-only probe URL in an isolated local configuration. Expected: Flow fails after Prefect retry, no login command runs, and one deduplicated infrastructure alert is recorded. Do not alter production URLs for this check.

- [ ] **Step 7: Commit documentation and verification updates**

```powershell
git add README.md PROJECT_GUIDE.md
git commit -m "docs: document session keeper operations"
```

---

## Completion Gate

Implementation is complete only when all of the following are evidenced:

- Authentication and infrastructure probe failures are classified correctly.
- Infrastructure failures never invoke login.
- Authentication failures invoke login immediately under the shared lock.
- Login waits 60 seconds after the first failure, retries once, and stops after two failures.
- Post-login Cookie and all three probes are valid before the session is published healthy.
- WeCom sends one failure alert per incident and one recovery message, with secrets redacted.
- Keeper runs at fixed quarter-hour boundaries and is queued once at startup.
- Business flows can refresh immediately once and retry only the failed step once.
- Focused and full tests pass.
- No session-lifetime experiment runner file was modified by this feature.
