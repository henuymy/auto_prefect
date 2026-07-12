# Simple Standalone Session Lifetime Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one local-only Python file that measures Session lifetime across four browser/session modes without locks, recovery machinery, Prefect, or internal background-process management.

**Architecture:** One foreground Python process runs four isolated Selenium login experiments sequentially. It captures the three required application sessions, probes them on fixed schedules, confirms authentication expiry with one retry, and writes append-only events plus an atomic summary. Windows `Start-Process` may place the ordinary foreground command in the background.

**Tech Stack:** Python 3.10+, Microsoft Edge, Selenium 4, Requests, Python standard library, pytest for local tests that do not contact production services.

## Global Constraints

- Create exactly one runtime program at the repository root: `session_lifetime_runner.py`.
- The program must not import any module from this repository.
- Runtime dependencies are limited to `selenium`, `requests`, and the Python standard library.
- Embed the effective local username, password, and OTP access configuration directly in the ignored Python file without printing them.
- Never commit, stage, log, or include plaintext credentials, OTP values, full Cookies, or storage tokens in result files.
- Keep the existing project runner and all existing experiment output unchanged.
- Run exactly four cases in this order: `headless_retained_idle`, `headed_closed_idle`, `headed_retained_idle`, `headless_closed_heartbeat`.
- Probe every 15 minutes; retry an authentication failure after 3 minutes; two consecutive authentication failures advance to the next case.
- Run a heartbeat every 5 minutes only in `headless_closed_heartbeat`.
- Infrastructure failures retry after 3 minutes and never count as Session expiry.
- A valid case runs indefinitely.
- Do not implement a lock file, singleton enforcement, PID recovery, interrupted-run recovery, internal background launcher, status command, stop command, Prefect integration, or automatic restart.
- Automated tests must not perform a real login or production HTTP request.

---

### Task 1: Protect the Local Runner and Establish Pure-Logic Tests

**Files:**
- Modify: `.gitignore`
- Create locally, ignored: `session_lifetime_runner.py`
- Create locally, ignored: `.session-lifetime-runner-tests/test_runner.py`

**Interfaces:**
- Produces: `ExperimentCase` dataclass.
- Produces: `CASES: tuple[ExperimentCase, ...]` with the exact four-case order.
- Produces: `next_fixed_probe_time(now: datetime, interval_minutes: int = 15) -> datetime`.
- Produces: `next_probe_decision(previous_failures: int, classification: str) -> dict`.
- Produces: `redact(value: object) -> object`.
- Produces: `write_json_atomic(path: Path, payload: dict) -> None`.

- [ ] **Step 1: Write failing local tests**

Create the ignored test module with an `importlib.util` loader for the root
runner and tests that assert:

```python
def test_sensitive_paths_are_git_ignored():
    assert check_ignored("session_lifetime_runner.py")
    assert check_ignored("session_lifetime_results/example/results.json")
    assert check_ignored(".session-lifetime-runner-tests/test_runner.py")


def test_cases_have_exact_order_and_modes():
    runner = load_runner()
    assert [case.name for case in runner.CASES] == [
        "headless_retained_idle",
        "headed_closed_idle",
        "headed_retained_idle",
        "headless_closed_heartbeat",
    ]
    assert [(case.headless, case.retain_browser, case.heartbeat_seconds) for case in runner.CASES] == [
        (True, True, None),
        (False, False, None),
        (False, True, None),
        (True, False, 300),
    ]


def test_probe_time_aligns_to_next_quarter_hour():
    runner = load_runner()
    now = runner.datetime.fromisoformat("2026-07-12T19:13:53+08:00")
    assert runner.next_fixed_probe_time(now).isoformat() == "2026-07-12T19:15:00+08:00"


def test_two_authentication_failures_advance_case():
    runner = load_runner()
    first = runner.next_probe_decision(0, "authentication_failure")
    second = runner.next_probe_decision(first["failures"], "authentication_failure")
    assert first == {"failures": 1, "advance": False, "retry_seconds": 180}
    assert second == {"failures": 2, "advance": True, "retry_seconds": None}


def test_valid_probe_resets_failures_and_infrastructure_does_not_count():
    runner = load_runner()
    assert runner.next_probe_decision(1, "valid")["failures"] == 0
    assert runner.next_probe_decision(1, "infrastructure_failure") == {
        "failures": 1,
        "advance": False,
        "retry_seconds": 180,
    }
```

Also test recursive redaction and two consecutive atomic JSON replacements.

- [ ] **Step 2: Run tests and verify expected failure**

```powershell
python -m pytest -p no:cacheprovider .session-lifetime-runner-tests/test_runner.py -q
```

Expected: failures because ignore rules and runner interfaces do not exist.

- [ ] **Step 3: Add exact Git protection**

Append:

```gitignore
# Local standalone Session lifetime experiment contains plaintext credentials
/session_lifetime_runner.py
/session_lifetime_results/
/.session-lifetime-runner-tests/
```

- [ ] **Step 4: Implement the minimal pure foundation**

Define:

```python
@dataclass(frozen=True)
class ExperimentCase:
    name: str
    headless: bool
    retain_browser: bool
    heartbeat_seconds: int | None


CASES = (
    ExperimentCase("headless_retained_idle", True, True, None),
    ExperimentCase("headed_closed_idle", False, False, None),
    ExperimentCase("headed_retained_idle", False, True, None),
    ExperimentCase("headless_closed_heartbeat", True, False, 300),
)
```

Implement fixed-quarter scheduling, the exact failure transition rules,
recursive sensitive-field redaction, credential-string replacement
longest-first, JSON reading, and temporary-file plus `os.replace` atomic JSON
writing.

- [ ] **Step 5: Run tests and commit only `.gitignore`**

Expected: all Task 1 tests pass. Verify the runner and tests appear only as
ignored, then commit:

```powershell
git add .gitignore
git commit -m "chore: ignore standalone session experiment secrets"
```

---

### Task 2: Implement Probe Requests and Lifetime Result Calculation

**Files:**
- Modify locally, ignored: `session_lifetime_runner.py`
- Modify locally, ignored: `.session-lifetime-runner-tests/test_runner.py`

**Interfaces:**
- Produces: `classify_probe_result(result: dict) -> str`.
- Produces: `execute_stage_probe(stage_name: str, snapshot: dict, session: requests.Session) -> dict`.
- Produces: `probe_all_stages(cookie_dump: dict, session: requests.Session | None = None) -> dict`.
- Produces: `build_lifetime_result(case: ExperimentCase, events: list[dict]) -> dict`.
- Produces: `append_jsonl(path: Path, payload: dict) -> None`.

- [ ] **Step 1: Write failing fake-response tests**

Test the three exact stage contracts with fake Requests sessions:

- `report_analysis`: POST, Cookie-derived `Ssr-Token`, JSON
  `returnCode == "0"`.
- `smart_ops`: GET, storage-derived `user-info`, HTTP 200 may have an empty
  body.
- `city_ops`: POST, storage-derived `Uaptoken`, JSON `reCode == "0000"`.

Assert HTTP 401/403, login redirects, `reCode=1101`, and known SSO timeout text
classify as `authentication_failure`. Assert Requests timeout, connection, DNS,
and TLS exceptions classify as `infrastructure_failure`.

Test lifetime bounds with events at 3:45 successful, 4:00 first auth failure,
and 4:03 retry auth failure:

```python
assert result["lower_bound_seconds"] == 13500
assert result["upper_bound_seconds"] == 14400
assert result["confirmed_at_seconds"] == 14580
```

- [ ] **Step 2: Run tests and verify failure**

Expected: Task 1 passes and new probe/result tests fail for missing interfaces.

- [ ] **Step 3: Implement probes and sanitized results**

Embed the three approved internal probe definitions. Build request Cookies,
headers, and storage-derived tokens from the private case snapshot. Use an
8-second timeout. Full token/Cookie values may enter Requests arguments but
must never enter returned event dictionaries or exception text.

Return per-stage events containing only stage name, timestamp, elapsed seconds,
HTTP status, `ok`, classification, duration, and sanitized reason.

`build_lifetime_result` must report the last successful fixed probe as the
lower bound and the first authentication failure as the upper bound. The retry
time confirms expiry but does not replace the upper bound.

- [ ] **Step 4: Run all local tests**

Expected: all tests pass without network access.

---

### Task 3: Implement Independent Selenium Login and Session Capture

**Files:**
- Modify locally, ignored: `session_lifetime_runner.py`
- Modify locally, ignored: `.session-lifetime-runner-tests/test_runner.py`

**Interfaces:**
- Produces: `build_driver(case: ExperimentCase, case_dir: Path) -> webdriver.Edge`.
- Produces: `fetch_otp(triggered_after: datetime) -> str`.
- Produces: `build_cookie_dump(captured_stages: list[dict]) -> dict`.
- Produces: `login_and_capture(case: ExperimentCase, case_dir: Path) -> tuple[dict, webdriver.Edge | None]`.

- [ ] **Step 1: Write failing configuration, driver, and capture tests**

Use fake Selenium objects to assert:

- Every case uses `case_dir / "edge_profile"` as its isolated profile.
- Headless cases add `--headless=new`; headed cases do not.
- Retained cases return a driver; closed cases call `quit()` and return `None`.
- The private dump contains exactly `report_analysis`, `smart_ops`, and
  `city_ops` with Cookies and required Session Storage.
- Redacted snapshots contain no Cookie values, storage tokens, password, OTP,
  or username.

- [ ] **Step 2: Run tests and verify failure**

Expected: new login/capture tests fail for missing interfaces.

- [ ] **Step 3: Embed effective private configuration**

Read the effective values from the main checkout's private
`config/modules/login_config.json` plus `login_config.local.json` using
recursive override semantics. Assign the effective username, password, login
URL, application definitions, and OTP access values directly as literals in
the ignored runner. Never print or copy their literal values into tests,
reports, patches, commits, or review packages.

- [ ] **Step 4: Port only the required login path**

Implement the current production path proven by `services/login_service.py`:

1. Start isolated Edge with the existing safe options and timeouts.
2. Load the login page and enter the embedded username/password.
3. Handle an existing-session confirmation dialog when present.
4. Request and fetch the OTP through the effective configured mode without
   logging the code.
5. Enter the main application and open the three required applications.
6. Capture Selenium Cookies and `zhyyptInfo.accessToken` / `uapToken` storage
   values.
7. Atomically write the private `cookie_dump.json` and a redacted
   `session_snapshot.json`.
8. Keep or close Edge according to the case.

Do not port data-market behavior, configuration loading, browser PID scanning,
or project service abstractions.

- [ ] **Step 5: Run local tests and syntax validation**

```powershell
python -m pytest -p no:cacheprovider .session-lifetime-runner-tests/test_runner.py -q
python -m py_compile session_lifetime_runner.py
```

Expected: all tests pass and compilation succeeds; no real login runs yet.

---

### Task 4: Implement the Sequential Experiment Loop and Validate One Login

**Files:**
- Modify locally, ignored: `session_lifetime_runner.py`
- Modify locally, ignored: `.session-lifetime-runner-tests/test_runner.py`

**Interfaces:**
- Produces: `run_case(case: ExperimentCase, run_dir: Path) -> dict`.
- Produces: `run_experiments() -> dict`.
- Produces: `main() -> int`.

- [ ] **Step 1: Write failing orchestration tests with fake clock/login/probes**

Assert that:

- Cases start strictly in the declared order.
- A valid case schedules another fixed 15-minute probe and never advances.
- One authentication failure schedules a 3-minute retry.
- The second authentication failure writes a result and advances once.
- Infrastructure failures schedule 3-minute retries without incrementing the
  authentication count.
- Only the heartbeat case emits heartbeat events every 300 seconds.
- Results are updated atomically after every completed case.

- [ ] **Step 2: Run tests and verify failure**

Expected: orchestration tests fail for missing runner functions.

- [ ] **Step 3: Implement the simple foreground loop**

At startup create:

```text
session_lifetime_results/YYYYMMDD_HHMMSS/
```

For each case, login once, record the initial probe, retain the driver object
only when required, wait interruptibly in at most 30-second sleeps, run fixed
probes and optional heartbeat, confirm expiry, write the lifetime interval,
call `driver.quit()` when a retained case ends, and continue to the next case.

Handle `KeyboardInterrupt` by writing a sanitized `runner_stopped` event,
quitting the currently owned driver, and exiting with code 130. Other unhandled
exceptions write a sanitized failure event and exit nonzero. Do not restart or
resume.

- [ ] **Step 4: Run final automated verification**

```powershell
python -m pytest -p no:cacheprovider .session-lifetime-runner-tests/test_runner.py -q
python -m py_compile session_lifetime_runner.py
git check-ignore -v session_lifetime_runner.py session_lifetime_results .session-lifetime-runner-tests
git status --short
```

Expected: all tests pass; sensitive paths are ignored; only the intentional
`.gitignore` commit is tracked for this feature.

- [ ] **Step 5: Run one controlled manual acceptance login**

Run `python session_lifetime_runner.py`, observe the first case complete one
login and initial three-stage probe, verify the retained headless Edge remains
alive, then stop with `Ctrl+C`. Confirm logs/results contain no secrets and the
owned driver exits.

- [ ] **Step 6: Document the Windows background command in the handoff**

Provide this command without adding background logic to Python:

```powershell
Start-Process python -ArgumentList "session_lifetime_runner.py" -WindowStyle Hidden -RedirectStandardOutput "runner.log" -RedirectStandardError "runner-error.log"
```
