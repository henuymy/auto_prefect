# Standalone Session Lifetime Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one local-only Python file that independently logs in, runs the four sequential session lifetime experiments, survives runner restarts, and supports foreground, background, status, validation, and graceful-stop commands.

**Architecture:** The ignored standalone file contains configuration, pure scheduling/state helpers, a PID-aware singleton lock, Windows process control, Selenium login and Cookie capture, HTTP probes, case orchestration, recovery, and CLI dispatch. Runtime state lives under an ignored `session_lifetime_runtime/` directory beside the script. Because the file contains plaintext credentials, implementation and tests remain untracked local artifacts; only repository ignore protection and documentation are committed.

**Tech Stack:** Python 3.10+, Windows PowerShell/CIM process inspection, Microsoft Edge, Selenium 4, Requests, pytest for local non-login tests.

## Global Constraints

- Create exactly one runtime program: `session_lifetime_runner_standalone.py` at the repository root.
- The program must not import any module from this repository.
- External runtime dependencies are limited to `selenium` and `requests` plus the Python standard library.
- Embed the current local login credentials and required login/OTP configuration directly in the Python configuration block without printing them.
- Never commit, stage, log, or include the plaintext credentials or full Cookie values in status output.
- Keep the existing `scripts/tools/session_lifetime_sequential_runner.py` and existing experiment output unchanged.
- Default probe interval is 15 minutes; an authentication failure is retried after 3 minutes; two consecutive authentication failures advance to the next case.
- Infrastructure failures retry every 3 minutes indefinitely and do not trigger login or case advancement.
- A valid case runs indefinitely.
- Browser cleanup may terminate only Edge processes whose command line contains the case's resolved user-data directory.
- Automated tests must not perform a real login or contact production probe endpoints.

---

### Task 1: Protect Local Standalone Artifacts and Create the Test Harness

**Files:**
- Modify: `.gitignore`
- Create locally, ignored: `session_lifetime_runner_standalone.py`
- Create locally, ignored: `.standalone-runner-tests/test_runner.py`

**Interfaces:**
- Produces: ignored source path `ROOT / "session_lifetime_runner_standalone.py"`.
- Produces: ignored runtime root `ROOT / "session_lifetime_runtime"`.
- Produces: import helper in the local test file that loads the standalone source with `importlib.util.spec_from_file_location`.

- [ ] **Step 1: Add failing ignore-contract test**

Create `.standalone-runner-tests/test_runner.py` with:

```python
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "session_lifetime_runner_standalone.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("standalone_runner", SOURCE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_sensitive_standalone_files_are_git_ignored():
    for path in (
        "session_lifetime_runner_standalone.py",
        "session_lifetime_runtime/state.json",
        ".standalone-runner-tests/test_runner.py",
    ):
        result = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0, path
```

- [ ] **Step 2: Run the ignore-contract test and verify failure**

Run:

```powershell
python -m pytest -p no:cacheprovider .standalone-runner-tests/test_runner.py::test_sensitive_standalone_files_are_git_ignored -q
```

Expected: FAIL because the three paths are not all ignored.

- [ ] **Step 3: Add exact ignore rules**

Append to `.gitignore`:

```gitignore
# Local standalone session lifetime runner contains plaintext credentials
/session_lifetime_runner_standalone.py
/session_lifetime_runtime/
/.standalone-runner-tests/
```

- [ ] **Step 4: Run the ignore-contract test**

Run the Step 2 command.

Expected: `1 passed`.

- [ ] **Step 5: Commit only the protection rule**

```powershell
git add .gitignore
git commit -m "chore: ignore standalone session runner secrets"
```

Verify that `git status --short --ignored` marks the standalone source, runtime, and local tests with `!!` once created.

---

### Task 2: Implement Configuration, Redaction, Atomic State, and PID-Aware Locking

**Files:**
- Create locally, ignored: `session_lifetime_runner_standalone.py`
- Modify locally, ignored: `.standalone-runner-tests/test_runner.py`

**Interfaces:**
- Produces: `RunnerConfig`, `ExperimentCase`, and `ProcessIdentity` dataclasses.
- Produces: `write_json_atomic(path: Path, payload: dict) -> None`.
- Produces: `redact(value: object) -> object` and `sanitize_error(exc: BaseException) -> str`.
- Produces: `current_process_identity() -> ProcessIdentity` and `same_live_process(identity: ProcessIdentity) -> bool`.
- Produces: `SingletonLock.acquire()`, `SingletonLock.release()`, and context-manager methods.

- [ ] **Step 1: Add failing pure-unit tests**

Append tests that assert:

```python
def test_redaction_removes_credentials_and_cookie_values(tmp_path):
    runner = load_runner()
    runner.LOGIN_USERNAME = "plain-user"
    runner.LOGIN_PASSWORD = "plain-password"
    raw = {
        "username": "plain-user",
        "password": "plain-password",
        "cookies": [{"name": "SSO", "value": "cookie-secret"}],
        "headers": {"Uaptoken": "token-secret"},
    }
    rendered = str(runner.redact(raw))
    assert "plain-user" not in rendered
    assert "plain-password" not in rendered
    assert "cookie-secret" not in rendered
    assert "token-secret" not in rendered


def test_atomic_state_replaces_complete_json(tmp_path):
    runner = load_runner()
    path = tmp_path / "state.json"
    runner.write_json_atomic(path, {"generation": 1})
    runner.write_json_atomic(path, {"generation": 2, "status": "running"})
    assert runner.read_json(path) == {"generation": 2, "status": "running"}
    assert not path.with_suffix(".json.tmp").exists()


def test_lock_rejects_same_live_owner(monkeypatch, tmp_path):
    runner = load_runner()
    identity = runner.ProcessIdentity(pid=321, started_at="2026-07-12T10:00:00+08:00")
    lock_path = tmp_path / "runner.lock"
    runner.write_json_atomic(lock_path, {**identity.as_dict(), "owner_token": "first"})
    monkeypatch.setattr(runner, "same_live_process", lambda value: True)
    lock = runner.SingletonLock(lock_path, wait_seconds=0)
    try:
        lock.acquire()
    except runner.AlreadyRunningError:
        pass
    else:
        raise AssertionError("live owner lock must be rejected")


def test_lock_replaces_dead_owner(monkeypatch, tmp_path):
    runner = load_runner()
    lock_path = tmp_path / "runner.lock"
    runner.write_json_atomic(lock_path, {"pid": 321, "started_at": "old", "owner_token": "old"})
    monkeypatch.setattr(runner, "same_live_process", lambda value: False)
    lock = runner.SingletonLock(lock_path, wait_seconds=0)
    lock.acquire()
    assert runner.read_json(lock_path)["owner_token"] == lock.owner_token
    lock.release()
    assert not lock_path.exists()
```

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```powershell
python -m pytest -p no:cacheprovider .standalone-runner-tests/test_runner.py -q
```

Expected: the ignore test passes and the new tests fail because the standalone module does not yet define the interfaces.

- [ ] **Step 3: Implement the configuration and state foundation**

Create the standalone source with these exact top-level groups:

```python
PROBE_INTERVAL_MINUTES = 15
RETRY_INTERVAL_MINUTES = 3
MAX_CONSECUTIVE_AUTH_FAILURES = 2
RUNTIME_ROOT = Path(__file__).resolve().with_name("session_lifetime_runtime")
```

Immediately above those public timing constants, define `LOGIN_USERNAME`,
`LOGIN_PASSWORD`, `GOTIFY_URL`, and `GOTIFY_CLIENT_TOKEN` as string literals.
Read their values from the current local private configuration at
implementation time, assign them without printing them, and never reproduce
their values in this plan, tool summaries, tests, logs, or commits. The
completed ignored file must contain nonempty username and password literals;
Gotify values must match the current local OTP configuration.

Define:

```python
@dataclass(frozen=True)
class ExperimentCase:
    name: str
    headless: bool
    retain_browser: bool
    heartbeat_interval_seconds: int | None


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    started_at: str

    def as_dict(self) -> dict:
        return {"pid": self.pid, "started_at": self.started_at}


class AlreadyRunningError(RuntimeError):
    pass
```

Use `os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)` for atomic lock
ownership. On Windows, query `Win32_Process` through a PowerShell subprocess
and compare both PID and creation time. Never evict a matching live owner.
Only the owner token that created the lock may remove it.

Redaction must recursively replace values under names containing
`password`, `username`, `cookie`, `token`, `authorization`, or `secret`.
It must also replace occurrences of the configured username and password in
exception strings.

- [ ] **Step 4: Run the local unit tests**

Run the Step 2 command.

Expected: all current tests pass.

- [ ] **Step 5: Verify Git protection**

Run:

```powershell
git status --short --ignored -- session_lifetime_runner_standalone.py .standalone-runner-tests session_lifetime_runtime
```

Expected: the source and local tests appear only with `!!`; no credential-bearing file appears as tracked or untracked.

---

### Task 3: Implement Scheduling, Probe Classification, and Case-State Transitions

**Files:**
- Modify locally, ignored: `session_lifetime_runner_standalone.py`
- Modify locally, ignored: `.standalone-runner-tests/test_runner.py`

**Interfaces:**
- Produces: `next_fixed_probe_time(now: datetime, interval_minutes: int = 15) -> datetime`.
- Produces: `classify_probe_result(results: list[dict]) -> str`, returning `valid`, `authentication_failure`, `infrastructure_failure`, or `browser_failure`.
- Produces: `next_case_decision(previous_auth_failures: int, classification: str) -> dict`.
- Produces: `interruptible_wait(target: datetime, stop_path: Path, tick_seconds: float = 30) -> bool`.

- [ ] **Step 1: Add failing scheduling and classification tests**

Append:

```python
def test_fixed_probes_align_to_quarter_hour():
    runner = load_runner()
    now = runner.datetime.fromisoformat("2026-07-12T19:13:53+08:00")
    assert runner.next_fixed_probe_time(now).isoformat() == "2026-07-12T19:15:00+08:00"


def test_authentication_requires_two_consecutive_failures():
    runner = load_runner()
    first = runner.next_case_decision(0, "authentication_failure")
    second = runner.next_case_decision(first["auth_failures"], "authentication_failure")
    assert first == {"auth_failures": 1, "advance": False, "retry_minutes": 3}
    assert second == {"auth_failures": 2, "advance": True, "retry_minutes": None}


def test_success_resets_authentication_failure_count():
    runner = load_runner()
    assert runner.next_case_decision(1, "valid")["auth_failures"] == 0


def test_infrastructure_failure_never_advances_case():
    runner = load_runner()
    decision = runner.next_case_decision(1, "infrastructure_failure")
    assert decision == {"auth_failures": 1, "advance": False, "retry_minutes": 3}


def test_browser_failure_uses_two_observation_confirmation():
    runner = load_runner()
    first = runner.next_case_decision(0, "browser_failure")
    second = runner.next_case_decision(first["auth_failures"], "browser_failure")
    assert first["advance"] is False
    assert second["advance"] is True
```

- [ ] **Step 2: Run the tests and verify failure**

Run the full local test command from Task 2.

Expected: new tests fail for missing scheduling and transition functions.

- [ ] **Step 3: Implement pure scheduling and transition logic**

Define default cases exactly as:

```python
CASES = (
    ExperimentCase("headless_retained_idle", True, True, None),
    ExperimentCase("headed_closed_idle", False, False, None),
    ExperimentCase("headed_retained_idle", False, True, None),
    ExperimentCase("headless_closed_heartbeat", True, False, 300),
)
```

`next_fixed_probe_time` must round strictly forward to the next wall-clock
multiple of 15 minutes. `next_case_decision` must preserve the authentication
failure count across infrastructure failures, reset it on valid probes, and
count browser failures only for cases requiring a retained browser.

`classify_probe_result` must distinguish authentication evidence such as HTTP
401/403, login-page redirects, `reCode=1101`, `session_expired`, and known
single-sign-on timeout text from transport exceptions such as DNS, connection,
TLS, VPN, and timeout failures.

- [ ] **Step 4: Run the local unit tests**

Expected: all tests pass.

---

### Task 4: Implement Edge Ownership, Selenium Login, OTP, and Cookie Capture

**Files:**
- Modify locally, ignored: `session_lifetime_runner_standalone.py`
- Modify locally, ignored: `.standalone-runner-tests/test_runner.py`

**Interfaces:**
- Produces: `find_owned_edge_processes(profile_dir: Path) -> list[dict]`.
- Produces: `close_owned_edge(profile_dir: Path, wait_seconds: int = 15) -> dict`.
- Produces: `build_edge_driver(case: ExperimentCase, case_dir: Path) -> webdriver.Edge`.
- Produces: `fetch_otp(triggered_after: datetime) -> str`.
- Produces: `login_and_capture(case: ExperimentCase, case_dir: Path) -> dict`.
- Produces full Cookie dump schema with `generated_at` and per-stage `cookies`, `session_storage`, and URL metadata.

- [ ] **Step 1: Add failing ownership and Cookie-schema tests**

Use monkeypatched PowerShell process output to verify that only command lines
containing the exact resolved case profile are returned. Add a test that calls
the Cookie-dump builder with fake Selenium cookies and storage values and
asserts:

```python
assert [stage["stage"] for stage in dump["stages"]] == [
    "report_analysis",
    "smart_ops",
    "city_ops",
]
assert dump["stages"][1]["session_storage"]["zhyyptInfo.accessToken"] == "smart-token"
assert dump["stages"][2]["session_storage"]["uapToken"] == "city-token"
```

Also assert that `redact(dump)` contains neither fake Cookie values nor fake
storage tokens.

- [ ] **Step 2: Run the tests and verify failure**

Expected: failures for missing process and Cookie-capture interfaces.

- [ ] **Step 3: Implement Windows Edge ownership**

Query `Win32_Process` as JSON using a fixed PowerShell command passed as an
argument array. Match the normalized, case-insensitive resolved profile path
inside `CommandLine`. Stop only returned PIDs with native PowerShell
`Stop-Process -Id <pid> -Force`; never enumerate one shell and delete in a
different shell. Poll until all owned PIDs exit or the timeout expires.

- [ ] **Step 4: Implement only the production login path used by current configuration**

Port the required Selenium behavior into the standalone file rather than
importing repository modules:

- Start Edge with the case's isolated `--user-data-dir` and configured
  headless/window settings.
- Navigate to the configured login URL.
- Enter the embedded username and password using the current page selectors.
- Submit credentials and handle the existing-session confirmation dialog.
- Fetch the Gotify OTP using the embedded endpoint/token, sender/title filters,
  freshness boundary, regex, timeout, and retry interval; submit it without
  logging the code.
- Open the current `report_analysis`, `smart_ops`, and `city_ops` application
  entries using the selectors already proven in `services/login_service.py`.
- Capture Selenium cookies and required session-storage values for each stage.
- Atomically write `cookie_dump.json`; write only redacted metadata to
  `session_snapshot.json`.
- Keep or close the driver according to `case.retain_browser`.

Do not port unused data-market behavior or configuration-file loading.

- [ ] **Step 5: Run local tests and perform syntax validation**

```powershell
python -m pytest -p no:cacheprovider .standalone-runner-tests/test_runner.py -q
python -m py_compile session_lifetime_runner_standalone.py
```

Expected: tests pass and compilation exits zero. Do not perform a real login yet.

---

### Task 5: Implement HTTP Probes, Heartbeat, Recovery, and Sequential Execution

**Files:**
- Modify locally, ignored: `session_lifetime_runner_standalone.py`
- Modify locally, ignored: `.standalone-runner-tests/test_runner.py`

**Interfaces:**
- Produces: `execute_stage_probe(stage_name: str, stage: dict, probe: dict, session: requests.Session) -> dict`.
- Produces: `probe_case(case: ExperimentCase, case_dir: Path) -> dict`.
- Produces: `recover_active_run(runtime_root: Path) -> dict | None`.
- Produces: `run_case(case: ExperimentCase, run_dir: Path, state: dict, stop_path: Path, *, resume: bool = False) -> dict`.
- Produces: `run_experiments(runtime_root: Path = RUNTIME_ROOT) -> dict`.

- [ ] **Step 1: Add failing probe and recovery tests**

Use fake response/session objects to test:

- `report_analysis` copies `Ssr-Token` from the named Cookie into headers and
  requires JSON `returnCode == "0"`.
- `smart_ops` copies `zhyyptInfo.accessToken` into `user-info` and accepts HTTP
  200 with an empty body.
- `city_ops` copies `uapToken` into `Uaptoken` and requires JSON
  `reCode == "0000"`.
- HTTP 401 is `authentication_failure`.
- `requests.Timeout` is `infrastructure_failure`.

Add recovery tests for these exact branches:

```python
def write_recovery_fixture(runner, tmp_path, *, browser_alive):
    run_dir = tmp_path / "runs" / "run-1"
    case_dir = run_dir / "headless_retained_idle"
    case_dir.mkdir(parents=True)
    runner.write_json_atomic(case_dir / "cookie_dump.json", {"stages": []})
    runner.write_json_atomic(
        run_dir / "state.json",
        {
            "status": "running",
            "pid": 999999,
            "process_started_at": "2026-07-12T10:00:00+08:00",
            "current_case": "headless_retained_idle",
            "phase": "waiting",
            "case_baseline_at": "2026-07-12T10:01:00+08:00",
            "consecutive_auth_failures": 0,
        },
    )
    runner.write_json_atomic(tmp_path / "active_run.json", {"run_dir": str(run_dir)})
    return run_dir, case_dir, browser_alive


def test_recovery_resumes_retained_case_when_cookie_and_browser_exist(monkeypatch, tmp_path):
    runner = load_runner()
    run_dir, case_dir, _ = write_recovery_fixture(runner, tmp_path, browser_alive=True)
    monkeypatch.setattr(runner, "same_live_process", lambda identity: False)
    monkeypatch.setattr(runner, "find_owned_edge_processes", lambda profile: [{"pid": 42}])
    result = runner.recover_active_run(tmp_path)
    assert result["action"] == "resume"
    assert Path(result["run_dir"]) == run_dir
    assert Path(result["case_dir"]) == case_dir


def test_recovery_restarts_retained_case_when_browser_is_dead(monkeypatch, tmp_path):
    runner = load_runner()
    run_dir, _, _ = write_recovery_fixture(runner, tmp_path, browser_alive=False)
    monkeypatch.setattr(runner, "same_live_process", lambda identity: False)
    monkeypatch.setattr(runner, "find_owned_edge_processes", lambda profile: [])
    result = runner.recover_active_run(tmp_path)
    assert result["action"] == "restart_case"
    assert Path(result["run_dir"]) == run_dir


def test_recovery_does_not_import_existing_project_runner_state(tmp_path):
    runner = load_runner()
    project_state = tmp_path / "runtime" / "session_experiments" / "old" / "state.json"
    project_state.parent.mkdir(parents=True)
    runner.write_json_atomic(project_state, {"status": "running"})
    result = runner.recover_active_run(tmp_path)
    assert result is None
```

- [ ] **Step 2: Run tests and verify failure**

Expected: probe and recovery tests fail for missing functions.

- [ ] **Step 3: Implement probe requests and heartbeat**

Embed the three probe definitions from the approved design. Build requests
from the stage Cookie/storage snapshot, use `verify=False` only for the known
internal host, suppress only the corresponding insecure-request warning, and
apply an 8-second timeout per stage.

Heartbeat must execute on its own 300-second cadence only for
`headless_closed_heartbeat`. Heartbeat events are marked separately and do not
replace the fixed 15-minute observation record. Infrastructure heartbeat
failures follow the infrastructure retry rule and do not count as Session
expiry.

- [ ] **Step 4: Implement resumable sequential orchestration**

Create a timestamped run directory under `session_lifetime_runtime/runs/` and
atomically update `session_lifetime_runtime/active_run.json`. Persist state at
least every 30 seconds while waiting. On restart, validate process identity,
Cookie presence, case phase, and required browser ownership before choosing
`resume` or `restart_case`.

When two confirmed authentication/browser failures end a case, append cleanup
and case-completed events before advancing. A valid case never advances. When
all four cases complete, write `summary.json` and set state to `completed`.

- [ ] **Step 5: Run all local tests**

Expected: all local tests pass without network or browser access.

---

### Task 6: Implement CLI, Background Launch, Status, Stop, and Validation

**Files:**
- Modify locally, ignored: `session_lifetime_runner_standalone.py`
- Modify locally, ignored: `.standalone-runner-tests/test_runner.py`

**Interfaces:**
- Produces: `validate_environment() -> list[dict]`.
- Produces: `launch_background() -> dict`.
- Produces: `read_status() -> dict`.
- Produces: `request_stop() -> dict`.
- Produces: `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Add failing CLI tests**

Test `main(["--status"])`, `main(["--stop"])`, and `main(["--check"])` with a
temporary runtime root and monkeypatched process/environment functions. Assert
that serialized output never contains the configured username, password, OTP
token, or Cookie values.

Test background command construction and assert it uses:

```text
<current python executable> <resolved standalone script> --run-child
```

On Windows it must pass `CREATE_NO_WINDOW | DETACHED_PROCESS` and redirect
stdout/stderr to files in the active run directory.

- [ ] **Step 2: Run tests and verify failure**

Expected: new CLI tests fail for missing functions/options.

- [ ] **Step 3: Implement commands**

Support exactly:

```text
no arguments   foreground execution
--background   detached execution and lock-acquisition verification
--run-child    internal detached child entrypoint
--status       read-only status
--stop         graceful stop request
--check        local dependency/configuration validation
```

`--background` waits up to 15 seconds for the child to acquire the singleton
lock and write a live state. If another live runner owns the lock, return a
concise already-running result instead of starting a duplicate.

`--stop` atomically writes `stop_request.json`. All waits check it at least
every 30 seconds. Stop cleanup must be limited to the active case's owned Edge
profile. `--status` must mark a recorded `running` state as `stale` when the
recorded process identity is no longer live.

`--check` verifies Python version, imports, Edge path, nonempty embedded
configuration, writable runtime path, probe structure, and redaction without
performing login or probes.

- [ ] **Step 4: Run local tests and command checks**

```powershell
python -m pytest -p no:cacheprovider .standalone-runner-tests/test_runner.py -q
python session_lifetime_runner_standalone.py --check
python session_lifetime_runner_standalone.py --status
```

Expected: tests pass; `--check` reports all local prerequisites healthy;
`--status` reports stopped/no active standalone run without changing state.

---

### Task 7: Security Review and Controlled Real Acceptance Test

**Files:**
- Verify locally, ignored: `session_lifetime_runner_standalone.py`
- Verify locally, ignored: `session_lifetime_runtime/`
- Verify: `.gitignore`

**Interfaces:**
- Consumes all earlier interfaces.
- Produces a locally usable, ignored standalone runner and validation evidence.

- [ ] **Step 1: Scan for repository imports and secret exposure**

Run:

```powershell
rg -n "from (services|utils|tasks|flows)|import (services|utils|tasks|flows)" session_lifetime_runner_standalone.py
git check-ignore -v session_lifetime_runner_standalone.py session_lifetime_runtime .standalone-runner-tests
git status --short
```

Expected: no repository imports; all sensitive paths are ignored; the
standalone file does not appear as trackable work.

- [ ] **Step 2: Run final automated verification**

```powershell
python -m pytest -p no:cacheprovider .standalone-runner-tests/test_runner.py -q
python -m py_compile session_lifetime_runner_standalone.py
python session_lifetime_runner_standalone.py --check
```

Expected: all tests pass, compilation succeeds, and prerequisite checks pass.

- [ ] **Step 3: Start one controlled foreground acceptance run**

Run from the same logged-in Windows user session that owns Edge:

```powershell
python session_lifetime_runner_standalone.py
```

Observe one successful login and the initial three-stage probe. Confirm the
console and runtime logs contain no username, password, OTP code, OTP token,
or full Cookie values. Stop gracefully with another terminal:

```powershell
python session_lifetime_runner_standalone.py --stop
```

Expected: state becomes `stopped`, the standalone-owned Edge processes exit,
and unrelated Edge processes remain alive.

- [ ] **Step 4: Verify background start and status**

```powershell
python session_lifetime_runner_standalone.py --background
python session_lifetime_runner_standalone.py --status
```

Expected: background command returns after a live child acquires the lock;
status reports the active case, latest probe, next action, PID, and browser
count without revealing secrets.

- [ ] **Step 5: Commit documentation-only completion metadata if needed**

Do not add the standalone source, runtime, or local tests to Git. Any final
documentation commit must contain no credentials, Cookie values, internal OTP
tokens, or generated runtime data.
