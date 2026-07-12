# Prefect Single-Windows Three-Pool Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the project on one interactive Windows host with three Prefect Process Workers, a shared external runtime directory, bounded notification concurrency of six, startup backlog cleanup, and project-owned lifecycle controls.

**Architecture:** One Prefect Server and PostgreSQL database serve three Work Pools: Session `1`, Dashboard `4`, and Notify `6`. PowerShell lifecycle scripts load the topology from `config/runtime.local.json`, reconcile stale scheduled runs before Workers start, launch one supervised Worker per Pool, and record project-owned process identities under `C:\AutoNotifyRuntime`. Python services resolve shared Cookie, browser, Session, and lock paths from the same runtime root.

**Tech Stack:** Windows PowerShell 7, Python 3.13, Prefect 3.7, PostgreSQL, MySQL, Selenium Edge, Microsoft Excel COM, pytest.

## Global Constraints

- Run all Prefect Workers under the same dedicated, logged-in Windows user.
- Keep one Prefect Server and one PostgreSQL metadata database.
- Use `windows-session-pool` with limit `1`, `windows-dashboard-pool` with limit `4`, and `windows-notify-pool` with limit `6`.
- Use `C:\AutoNotifyRuntime` as the default external runtime root.
- Keep Excel COM globally serial even when six Notify Flows are active.
- Keep automatic login globally serial across all Pools.
- Cancel auto-scheduled Notify runs more than `600` seconds late; preserve manual runs.
- Treat Session state as fresh for `180` seconds.
- Do not configure Windows Task Scheduler or automatic startup.
- Do not log or commit credentials, Cookies, Tokens, OTP values, or Webhook URLs.
- Preserve legacy local environment files only as migration fallbacks.
- Implement behavior with tests first and commit after every task.

---

### Task 1: Define the Runtime Topology Contract

**Files:**
- Modify: `config/runtime.local.example.json`
- Modify: `scripts/lib/runtime_config.ps1`
- Modify: `tests/test_development_environment_contract.py`

**Interfaces:**
- Produces: `$env:AUTO_NOTIFY_RUNTIME_ROOT`.
- Produces: `$env:PREFECT_SESSION_POOL_NAME`, `$env:PREFECT_SESSION_POOL_LIMIT`.
- Produces: `$env:PREFECT_DASHBOARD_POOL_NAME`, `$env:PREFECT_DASHBOARD_POOL_LIMIT`.
- Produces: `$env:PREFECT_NOTIFY_POOL_NAME`, `$env:PREFECT_NOTIFY_POOL_LIMIT`.
- Produces: `$env:AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS`.
- Produces: `$env:AUTO_NOTIFY_SESSION_FRESHNESS_SECONDS`.
- Preserves: `$env:PREFECT_WORK_POOL_NAME` as a compatibility alias for the Notify Pool during migration.

- [ ] **Step 1: Write failing configuration contract tests**

Replace the single `runtime.work_pool` assertions with explicit topology assertions:

```python
def test_runtime_json_template_exports_three_pool_topology_and_runtime_root():
    template = json.loads(
        (ROOT / "config" / "runtime.local.example.json").read_text(encoding="utf-8")
    )
    runtime = template["runtime"]

    assert runtime["root"] == r"C:\AutoNotifyRuntime"
    assert runtime["scheduled_notify_grace_seconds"] == 600
    assert runtime["session_freshness_seconds"] == 180
    assert runtime["work_pools"] == {
        "session": {"name": "windows-session-pool", "limit": 1},
        "dashboard": {"name": "windows-dashboard-pool", "limit": 4},
        "notify": {"name": "windows-notify-pool", "limit": 6},
    }


def test_runtime_loader_exports_three_pool_environment_contract():
    source = (ROOT / "scripts" / "lib" / "runtime_config.ps1").read_text(
        encoding="utf-8"
    )
    for variable in (
        "AUTO_NOTIFY_RUNTIME_ROOT",
        "PREFECT_SESSION_POOL_NAME",
        "PREFECT_SESSION_POOL_LIMIT",
        "PREFECT_DASHBOARD_POOL_NAME",
        "PREFECT_DASHBOARD_POOL_LIMIT",
        "PREFECT_NOTIFY_POOL_NAME",
        "PREFECT_NOTIFY_POOL_LIMIT",
        "AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS",
        "AUTO_NOTIFY_SESSION_FRESHNESS_SECONDS",
    ):
        assert variable in source
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
python -m pytest tests/test_development_environment_contract.py -k "runtime_json_template_exports_three_pool or runtime_loader_exports_three_pool" -q
```

Expected: failures because the example JSON and loader still expose only `runtime.work_pool`.

- [ ] **Step 3: Extend the example runtime JSON**

Use this exact runtime shape:

```json
"runtime": {
  "root": "C:\\AutoNotifyRuntime",
  "scheduled_notify_grace_seconds": 600,
  "session_freshness_seconds": 180,
  "work_pools": {
    "session": {"name": "windows-session-pool", "limit": 1},
    "dashboard": {"name": "windows-dashboard-pool", "limit": 4},
    "notify": {"name": "windows-notify-pool", "limit": 6}
  }
}
```

- [ ] **Step 4: Export the topology from `runtime_config.ps1`**

Add a positive integer helper and export exact values:

```powershell
function Get-RuntimeConfigPositiveInt {
    param([object]$Config, [string]$Path)
    $value = [int](Get-RuntimeConfigValue -Config $Config -Path $Path)
    if ($value -le 0) {
        throw "运行配置必须为正整数: $Path"
    }
    return $value
}

$env:AUTO_NOTIFY_RUNTIME_ROOT = Get-RuntimeConfigValue -Config $config -Path "runtime.root"
$env:PREFECT_SESSION_POOL_NAME = Get-RuntimeConfigValue -Config $config -Path "runtime.work_pools.session.name"
$env:PREFECT_SESSION_POOL_LIMIT = Get-RuntimeConfigPositiveInt -Config $config -Path "runtime.work_pools.session.limit"
$env:PREFECT_DASHBOARD_POOL_NAME = Get-RuntimeConfigValue -Config $config -Path "runtime.work_pools.dashboard.name"
$env:PREFECT_DASHBOARD_POOL_LIMIT = Get-RuntimeConfigPositiveInt -Config $config -Path "runtime.work_pools.dashboard.limit"
$env:PREFECT_NOTIFY_POOL_NAME = Get-RuntimeConfigValue -Config $config -Path "runtime.work_pools.notify.name"
$env:PREFECT_NOTIFY_POOL_LIMIT = Get-RuntimeConfigPositiveInt -Config $config -Path "runtime.work_pools.notify.limit"
$env:AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS = Get-RuntimeConfigPositiveInt -Config $config -Path "runtime.scheduled_notify_grace_seconds"
$env:AUTO_NOTIFY_SESSION_FRESHNESS_SECONDS = Get-RuntimeConfigPositiveInt -Config $config -Path "runtime.session_freshness_seconds"
$env:PREFECT_WORK_POOL_NAME = $env:PREFECT_NOTIFY_POOL_NAME
```

Reject duplicate Pool names after loading:

```powershell
$poolNames = @(
    $env:PREFECT_SESSION_POOL_NAME,
    $env:PREFECT_DASHBOARD_POOL_NAME,
    $env:PREFECT_NOTIFY_POOL_NAME
)
if (($poolNames | Select-Object -Unique).Count -ne 3) {
    throw "三个 Prefect Work Pool 名称必须互不相同"
}
```

Because `prefect.yaml` contains static Pool names, reject configuration drift:

```powershell
$expectedPools = @{
    session = "windows-session-pool"
    dashboard = "windows-dashboard-pool"
    notify = "windows-notify-pool"
}
if ($env:PREFECT_SESSION_POOL_NAME -ne $expectedPools.session -or
    $env:PREFECT_DASHBOARD_POOL_NAME -ne $expectedPools.dashboard -or
    $env:PREFECT_NOTIFY_POOL_NAME -ne $expectedPools.notify) {
    throw "runtime.local.json 的 Work Pool 名称必须与 prefect.yaml 的三 Pool 拓扑一致"
}
```

- [ ] **Step 5: Update the existing JSON precedence test and run the contract suite**

Update its temporary JSON fixture to the new runtime shape and assert all new environment values. Run:

```powershell
python -m pytest tests/test_development_environment_contract.py -k "runtime_json or runtime_loader" -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the runtime topology contract**

```powershell
git add config/runtime.local.example.json scripts/lib/runtime_config.ps1 tests/test_development_environment_contract.py
git commit -m "feat: define three-pool runtime topology"
```

---

### Task 2: Route Shared Runtime State Outside the Repository and Harden Excel Lock Ownership

**Files:**
- Create: `services/runtime_paths.py`
- Modify: `services/session_manager.py`
- Modify: `services/browser_session.py`
- Modify: `services/cookie_recorder.py`
- Modify: `infrastructure/excel_client.py`
- Modify: `config/modules/autologin.json`
- Modify: `config/modules/login_config.json`
- Modify: `config/modules/session_keeper.json`
- Modify: `config/modules/report_downloader.json`
- Modify: `config/dashboard/session.json`
- Modify: `tests/test_session_manager.py`
- Modify: `tests/test_login_service.py`
- Modify: `tests/test_excel_client.py`
- Create: `tests/test_runtime_paths.py`

**Interfaces:**
- Produces: `runtime_root(env: Mapping[str, str] | None = None) -> Path`.
- Produces: `resolve_runtime_path(value: str | Path | None, *, project_dir: Path) -> Path | None`.
- Produces: `runtime_path(relative: str | Path) -> Path`.
- Produces: `infrastructure.excel_client.process_is_running(pid: int, started_at: str | None = None) -> bool`.
- Produces: `infrastructure.excel_client.current_process_started_at() -> str`.
- Produces: Excel lock JSON containing `pid`, `process_started_at`, `owner_token`, and `acquired_at`.

- [ ] **Step 1: Write failing runtime path tests**

```python
def test_runtime_relative_paths_rebase_to_external_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))
    assert resolve_runtime_path("runtime/cookies/cookie_dump.json", project_dir=tmp_path) == (
        tmp_path / "shared" / "cookies" / "cookie_dump.json"
    ).resolve()


def test_non_runtime_relative_paths_remain_project_relative(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))
    assert resolve_runtime_path("config/tasks/demo.json", project_dir=tmp_path) == (
        tmp_path / "config" / "tasks" / "demo.json"
    ).resolve()


def test_absolute_paths_are_preserved(monkeypatch, tmp_path):
    target = (tmp_path / "absolute.json").resolve()
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))
    assert resolve_runtime_path(target, project_dir=tmp_path) == target
```

- [ ] **Step 2: Write failing Excel lock ownership tests**

```python
def test_excel_lock_does_not_evict_live_owner(monkeypatch, tmp_path):
    lock_path = tmp_path / "excel.lock"
    lock_path.write_text(
        json.dumps({"pid": 321, "process_started_at": "2026-07-12T10:00:00+08:00"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(excel_client, "process_is_running", lambda pid, started_at=None: True)
    assert excel_client.FileLock(lock_path, stale_seconds=1).is_stale() is False


def test_excel_lock_evicts_dead_owner(monkeypatch, tmp_path):
    lock_path = tmp_path / "excel.lock"
    lock_path.write_text(json.dumps({"pid": 321}), encoding="utf-8")
    monkeypatch.setattr(excel_client, "process_is_running", lambda pid, started_at=None: False)
    assert excel_client.FileLock(lock_path, stale_seconds=9999).is_stale() is True
```

- [ ] **Step 3: Run tests and verify failure**

```powershell
python -m pytest tests/test_runtime_paths.py tests/test_excel_client.py -q
```

Expected: failures because runtime path rebasing and PID-aware Excel locks do not exist.

- [ ] **Step 4: Implement the centralized runtime path resolver**

```python
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

PROJECT_DIR = Path(__file__).resolve().parents[1]


def runtime_root(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("AUTO_NOTIFY_RUNTIME_ROOT")
    return Path(configured).expanduser().resolve() if configured else (PROJECT_DIR / "runtime").resolve()


def runtime_path(relative: str | Path) -> Path:
    return (runtime_root() / Path(relative)).resolve()


def resolve_runtime_path(value, *, project_dir: Path = PROJECT_DIR):
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    parts = path.parts
    if parts and parts[0].lower() == "runtime":
        return (runtime_root() / Path(*parts[1:])).resolve()
    return (project_dir / path).resolve()
```

- [ ] **Step 5: Route Session, Cookie, browser, and Excel defaults through the resolver**

Replace project-relative `runtime/...` resolution in the listed services with `resolve_runtime_path`. Define Excel's default lazily so tests can change the environment:

```python
def default_excel_lock_path() -> Path:
    return runtime_path("locks/excel_com.lock")


class FileLock:
    def __init__(self, path=None, ...):
        self.path = Path(path) if path else default_excel_lock_path()
```

Keep the JSON configuration values as `runtime/...`; the resolver rebases them to `AUTO_NOTIFY_RUNTIME_ROOT`. Update `session_keeper.json` so its incident state remains `runtime/session_keeper/incident_state.json` and is therefore rebased consistently.

- [ ] **Step 6: Make Excel lock ownership PID-aware**

Write JSON rather than an ad hoc text line:

```python
payload = {
    "pid": _current_pid(),
    "process_started_at": current_process_started_at(),
    "owner_token": uuid4().hex,
    "acquired_at": datetime.now(timezone.utc).isoformat(),
}
self.handle.write(json.dumps(payload, ensure_ascii=False))
```

`FileLock.is_stale()` must return `False` for a live matching process regardless of lock age, return `True` for a dead owner, and use the age threshold only for legacy or corrupt files without a PID.

Implement `process_is_running` with `Get-Process -Id <pid>` on Windows. When `started_at` is present, compare it with the process `StartTime` converted to ISO 8601; a PID with a different start time is not the owner. `current_process_started_at()` reads the current process start time using the same representation.

- [ ] **Step 7: Run focused and regression tests**

```powershell
python -m pytest tests/test_runtime_paths.py tests/test_excel_client.py tests/test_session_manager.py tests/test_login_service.py -q
```

Expected: all selected tests pass without contacting production services.

- [ ] **Step 8: Commit shared runtime state routing**

```powershell
git add services/runtime_paths.py services/session_manager.py services/browser_session.py services/cookie_recorder.py infrastructure/excel_client.py config/modules/autologin.json config/modules/login_config.json config/modules/session_keeper.json config/modules/report_downloader.json config/dashboard/session.json tests/test_runtime_paths.py tests/test_session_manager.py tests/test_login_service.py tests/test_excel_client.py
git commit -m "feat: externalize shared Windows runtime state"
```

---

### Task 3: Cache Fresh Session Verification for Business Flows

**Files:**
- Create: `services/session_health_state.py`
- Modify: `services/session_manager.py`
- Modify: `config/modules/autologin.json`
- Modify: `tests/test_session_manager.py`
- Create: `tests/test_session_health_state.py`

**Interfaces:**
- Produces: `cookie_snapshot_hash(path: Path) -> str`.
- Produces: `read_session_health(path: Path) -> dict`.
- Produces: `write_session_health_atomic(path: Path, payload: dict) -> Path`.
- Produces: `session_health_is_fresh(state: dict, *, cookie_hash: str, required_stages: list[str], freshness_seconds: int, now: datetime | None = None) -> bool`.
- Consumes: `$env:AUTO_NOTIFY_SESSION_FRESHNESS_SECONDS` from Task 1.
- Stores: `runtime/session/session_state.json`, rebased by Task 2.

- [ ] **Step 1: Write failing health-state tests**

```python
def test_fresh_health_state_with_matching_cookie_hash_is_reusable():
    state = {
        "healthy": True,
        "verified_at": "2026-07-12T20:00:00+08:00",
        "cookie_hash": "abc",
        "healthy_stages": ["report_analysis", "smart_ops", "city_ops"],
    }
    assert session_health_is_fresh(
        state,
        cookie_hash="abc",
        required_stages=["report_analysis", "smart_ops"],
        freshness_seconds=180,
        now=datetime.fromisoformat("2026-07-12T20:02:59+08:00"),
    ) is True


def test_health_state_is_stale_on_age_hash_or_stage_mismatch():
    base = {
        "healthy": True,
        "verified_at": "2026-07-12T20:00:00+08:00",
        "cookie_hash": "abc",
        "healthy_stages": ["report_analysis"],
    }
    assert session_health_is_fresh(
        base,
        cookie_hash="abc",
        required_stages=["report_analysis"],
        freshness_seconds=180,
        now=datetime.fromisoformat("2026-07-12T20:03:01+08:00"),
    ) is False
    assert session_health_is_fresh(
        base,
        cookie_hash="different",
        required_stages=["report_analysis"],
        freshness_seconds=180,
    ) is False
    assert session_health_is_fresh(
        base,
        cookie_hash="abc",
        required_stages=["report_analysis", "city_ops"],
        freshness_seconds=180,
    ) is False
```

- [ ] **Step 2: Write failing Session Manager reuse tests**

```python
def session_config(cookie_dump_path, **overrides):
    config = {
        "cookie_dump_path": str(cookie_dump_path),
        "required_stages": ["city_ops"],
        "login_command": "fake-login",
        "login_max_attempts": 2,
        "login_retry_delay_seconds": 60,
        "stage_probes": {
            "city_ops": {"method": "POST", "url": "https://example/getUserInfo"}
        },
    }
    config.update(overrides)
    return config


def write_fresh_health_state(state_path, cookie_path):
    write_json(
        state_path,
        {
            "healthy": True,
            "verified_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "cookie_hash": cookie_snapshot_hash(cookie_path),
            "healthy_stages": ["city_ops"],
        },
    )


def test_prepare_session_skips_probes_when_keeper_state_is_fresh(monkeypatch, tmp_path):
    cookie_path = tmp_path / "cookie.json"
    state_path = tmp_path / "session_state.json"
    write_json(cookie_path, valid_city_ops_cookie_dump())
    write_fresh_health_state(state_path, cookie_path)
    probes = Mock(side_effect=AssertionError("fresh state must skip probes"))

    monkeypatch.setattr(session_manager, "validate_stage_probes", probes)
    result = session_manager.prepare_session(
        session_config(
            cookie_path,
            session_health_state_path=str(state_path),
            session_freshness_seconds=180,
        )
    )

    assert result["status"] == "reused_fresh"


def test_prepare_session_probes_stale_state_and_refreshes_health_file(monkeypatch, tmp_path):
    cookie_path = tmp_path / "cookie.json"
    state_path = tmp_path / "session_state.json"
    write_json(cookie_path, valid_city_ops_cookie_dump())
    probes = Mock(
        return_value={
            "valid": True,
            "results": [{"stage": "city_ops", "ok": True}],
        }
    )

    monkeypatch.setattr(session_manager, "validate_stage_probes", probes)
    result = session_manager.prepare_session(
        session_config(
            cookie_path,
            session_health_state_path=str(state_path),
            session_freshness_seconds=180,
        )
    )

    assert result["status"] == "reused"
    assert json.loads(state_path.read_text(encoding="utf-8"))["healthy"] is True
```

- [ ] **Step 3: Run tests and verify failure**

```powershell
python -m pytest tests/test_session_health_state.py tests/test_session_manager.py -k "health_state or fresh" -q
```

Expected: failures because the health snapshot module and freshness shortcut do not exist.

- [ ] **Step 4: Implement atomic health snapshots**

Write only sanitized metadata:

```python
payload = {
    "healthy": True,
    "verified_at": datetime.now(timezone.utc).astimezone().isoformat(),
    "cookie_hash": cookie_snapshot_hash(cookie_dump_path),
    "healthy_stages": sorted(required_stages),
}
```

Use a sibling temporary file and `os.replace`. Never copy Cookie content into the health file.

- [ ] **Step 5: Integrate freshness into `prepare_session`**

Read configuration as:

```python
session_health_state_path = resolve_runtime_path(
    config.get("session_health_state_path", "runtime/session/session_state.json"),
    project_dir=base_dir,
)
freshness_seconds = int(
    os.environ.get("AUTO_NOTIFY_SESSION_FRESHNESS_SECONDS")
    or config.get("session_freshness_seconds", 180)
)
```

After Cookie structure validation and before stage probes, return `reused_fresh` only when the health snapshot is fresh, has the current Cookie file hash, and covers all required stages. Otherwise run existing probes. Write a healthy snapshot after successful probes and after successful login publication. Write `healthy: false` with a sanitized classification after confirmed authentication failure.

- [ ] **Step 6: Add the default health state path to `autologin.json`**

```json
"session_health_state_path": "runtime/session/session_state.json",
"session_freshness_seconds": 180
```

- [ ] **Step 7: Run Session regression tests**

```powershell
python -m pytest tests/test_session_health_state.py tests/test_session_manager.py tests/test_session_keeper_flow.py tests/test_notify_flow_session.py tests/test_dashboard_metric_flow_session.py -q
```

Expected: all selected tests pass and no real login occurs.

- [ ] **Step 8: Commit Session freshness caching**

```powershell
git add services/session_health_state.py services/session_manager.py config/modules/autologin.json tests/test_session_health_state.py tests/test_session_manager.py
git commit -m "feat: reuse recently verified session state"
```

---

### Task 4: Assign Deployments and Dynamic Notify Publishing to Three Pools

**Files:**
- Modify: `prefect.yaml`
- Modify: `deployments/notify_single_deployment.yaml`
- Modify: `backend/services/prefect_runner.py`
- Modify: `scripts/tools/dashboard/v2_cutover_audit.py`
- Modify: `tests/test_development_environment_contract.py`
- Modify: `tests/test_prefect_runner.py`
- Modify: `tests/test_dashboard_v2_admin_scripts.py`

**Interfaces:**
- Consumes: Pool names exported by Task 1.
- Produces: static Deployment-to-Pool routing in `prefect.yaml`.
- Produces: `get_notify_work_pool() -> str` in `backend/services/prefect_runner.py`.

- [ ] **Step 1: Write failing Deployment routing tests**

```python
def test_prefect_deployments_are_partitioned_across_three_pools():
    config = yaml.safe_load((ROOT / "prefect.yaml").read_text(encoding="utf-8"))
    pools = {row["name"]: row["work_pool"]["name"] for row in config["deployments"]}

    assert pools["session-keeper"] == "windows-session-pool"
    assert pools["notify-daily"] == "windows-notify-pool"
    for name in (
        "dashboard-collection",
        "dashboard-daily-acc",
        "dashboard-monthly",
        "dashboard-indicator-sync",
        "dashboard-v2-partition-maintenance",
    ):
        assert pools[name] == "windows-dashboard-pool"


def test_dynamic_notify_publish_uses_notify_pool(monkeypatch):
    monkeypatch.setenv("PREFECT_NOTIFY_POOL_NAME", "notify-test-pool")
    assert prefect_runner.get_notify_work_pool() == "notify-test-pool"
```

- [ ] **Step 2: Run the tests and verify failure**

```powershell
python -m pytest tests/test_development_environment_contract.py tests/test_prefect_runner.py -k "partitioned_across_three_pools or dynamic_notify_publish" -q
```

Expected: failures because all Deployments and dynamic publishing still use `default-agent-pool`.

- [ ] **Step 3: Route static Deployments**

Set `work_pool.name` exactly as follows:

```yaml
session-keeper: windows-session-pool
notify-daily: windows-notify-pool
dashboard-collection: windows-dashboard-pool
dashboard-daily-acc: windows-dashboard-pool
dashboard-monthly: windows-dashboard-pool
dashboard-indicator-sync: windows-dashboard-pool
dashboard-v2-partition-maintenance: windows-dashboard-pool
```

Keep `work_queue_name: default` for all three Pools.

- [ ] **Step 4: Route dynamically published Notify Deployments**

Replace the constant with:

```python
DEFAULT_NOTIFY_WORK_POOL = "windows-notify-pool"


def get_notify_work_pool() -> str:
    return os.environ.get("PREFECT_NOTIFY_POOL_NAME") or DEFAULT_NOTIFY_WORK_POOL
```

Use `get_notify_work_pool()` wherever `prefect_runner.py` constructs a deployment build/apply command or response payload. Update `deployments/notify_single_deployment.yaml` to `windows-notify-pool`.

- [ ] **Step 5: Update the Dashboard cutover audit**

Read Workers only from `PREFECT_DASHBOARD_POOL_NAME`, defaulting to `windows-dashboard-pool`, instead of filtering for `default-agent-pool`.

- [ ] **Step 6: Run routing tests**

```powershell
python -m pytest tests/test_development_environment_contract.py tests/test_prefect_runner.py tests/test_dashboard_v2_admin_scripts.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit Deployment routing**

```powershell
git add prefect.yaml deployments/notify_single_deployment.yaml backend/services/prefect_runner.py scripts/tools/dashboard/v2_cutover_audit.py tests/test_development_environment_contract.py tests/test_prefect_runner.py tests/test_dashboard_v2_admin_scripts.py
git commit -m "feat: route deployments across three work pools"
```

---

### Task 5: Reconcile Expired Scheduled Runs Before Worker Startup

**Files:**
- Create: `scripts/lib/prefect_startup_reconcile.py`
- Create: `tests/test_prefect_startup_reconcile.py`
- Modify: `scripts/lib/prefect_start.ps1`
- Modify: `tests/test_development_environment_contract.py`
- Retire: `scripts/dev/clear_scheduled_backlog.ps1`

**Interfaces:**
- Produces: `class DeploymentPolicy(Enum)` with `SESSION`, `DASHBOARD_HIGH_FREQUENCY`, `DASHBOARD_SINGLETON`, and `NOTIFY`.
- Produces: `classify_deployment(name: str) -> DeploymentPolicy | None`.
- Produces: `should_cancel_run(run: Any, policy: DeploymentPolicy, now: datetime, notify_grace_seconds: int) -> tuple[bool, str]`.
- Produces CLI: `python scripts/lib/prefect_startup_reconcile.py --notify-grace-seconds 600`.
- Exit code `0`: reconciliation succeeded and no in-flight blockers exist.
- Exit code `2`: one or more managed runs are `RUNNING`, `CANCELLING`, or `PAUSED`.
- Exit code `1`: API or reconciliation failure.

- [ ] **Step 1: Write failing pure policy tests**

```python
def scheduled_run(*, minutes_late, auto_scheduled=True, state="SCHEDULED"):
    return SimpleNamespace(
        state=SimpleNamespace(type=SimpleNamespace(value=state)),
        expected_start_time=NOW - timedelta(minutes=minutes_late),
        auto_scheduled=auto_scheduled,
    )


def test_notify_more_than_ten_minutes_late_is_cancelled():
    cancel, reason = should_cancel_run(
        scheduled_run(minutes_late=11), DeploymentPolicy.NOTIFY, NOW, 600
    )
    assert cancel is True
    assert reason == "scheduled_notify_expired"


def test_notify_within_ten_minutes_is_preserved():
    assert should_cancel_run(
        scheduled_run(minutes_late=9), DeploymentPolicy.NOTIFY, NOW, 600
    )[0] is False


def test_manual_notify_is_never_cancelled_by_lateness():
    assert should_cancel_run(
        scheduled_run(minutes_late=120, auto_scheduled=False),
        DeploymentPolicy.NOTIFY,
        NOW,
        600,
    )[0] is False


def test_expired_session_and_high_frequency_dashboard_runs_are_cancelled():
    run = scheduled_run(minutes_late=1)
    assert should_cancel_run(run, DeploymentPolicy.SESSION, NOW, 600)[0] is True
    assert should_cancel_run(run, DeploymentPolicy.DASHBOARD_HIGH_FREQUENCY, NOW, 600)[0] is True
```

- [ ] **Step 2: Add singleton backlog tests**

For daily/monthly/maintenance deployments, sort auto-scheduled overdue runs newest-first, preserve at most the newest run, and cancel older duplicates. Future scheduled runs are always preserved.

- [ ] **Step 3: Run tests and verify failure**

```powershell
python -m pytest tests/test_prefect_startup_reconcile.py -q
```

Expected: import failure because the reconciliation module does not exist.

- [ ] **Step 4: Implement the pure classification and cancellation rules**

Use these exact deployment sets:

```python
SESSION_DEPLOYMENTS = {"session-keeper"}
DASHBOARD_HIGH_FREQUENCY_DEPLOYMENTS = {"dashboard-collection"}
DASHBOARD_SINGLETON_DEPLOYMENTS = {
    "dashboard-daily-acc",
    "dashboard-monthly",
    "dashboard-indicator-sync",
    "dashboard-v2-partition-maintenance",
}


def is_manual_run(run) -> bool:
    return not bool(getattr(run, "auto_scheduled", False))
```

Notify deployments are names equal to `notify-daily` or beginning with `notify-`. Only auto-scheduled Notify runs are subject to the 600-second rule.

- [ ] **Step 5: Implement Prefect API reconciliation**

The command must:

1. Read managed Deployments.
2. Pause only Deployments that were active when reconciliation started.
3. Read `SCHEDULED` and `PENDING` runs in pages of 200.
4. Cancel selected runs with `Cancelled(message=<reason>)` and `force=True`.
5. Read `RUNNING`, `CANCELLING`, and `PAUSED` runs.
6. Exit `2` and list blockers if any exist.
7. Resume only Deployments paused by this invocation in a `finally` block.

Do not delete Flow Run records; cancellation preserves the audit trail.

- [ ] **Step 6: Replace the embedded Dashboard-only cleanup**

Remove `Prepare-DashboardWorkerStart` from `prefect_start.ps1`. `run.ps1` will invoke the new reconciliation command exactly once before any Worker starts in Task 5. Delete the deprecated `scripts/dev/clear_scheduled_backlog.ps1` after tests confirm no public entry point references it.

- [ ] **Step 7: Run reconciliation and lifecycle contract tests**

```powershell
python -m pytest tests/test_prefect_startup_reconcile.py tests/test_development_environment_contract.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit startup reconciliation**

```powershell
git add scripts/lib/prefect_startup_reconcile.py scripts/lib/prefect_start.ps1 tests/test_prefect_startup_reconcile.py tests/test_development_environment_contract.py
git rm scripts/dev/clear_scheduled_backlog.ps1
git commit -m "feat: reconcile stale Prefect runs at startup"
```

---

### Task 6: Start and Supervise Three Bounded Workers

**Files:**
- Modify: `scripts/lib/prefect_start.ps1`
- Modify: `scripts/run.ps1`
- Modify: `tests/test_development_environment_contract.py`

**Interfaces:**
- Consumes: topology environment variables from Task 1.
- Consumes: startup reconciliation CLI from Task 5.
- Produces: `prefect_start.ps1 -Mode worker -WorkPool <name> -WorkerLimit <n>`.
- Produces: one supervised detached Worker per configured Pool.

- [ ] **Step 1: Write failing lifecycle ordering tests**

```python
def test_run_script_reconciles_then_starts_three_bounded_workers():
    source = (ROOT / "scripts" / "run.ps1").read_text(encoding="utf-8")
    lowered = source.lower()

    reconcile = "prefect_startup_reconcile.py"
    assert source.count("-Mode worker") == 3
    assert "$env:PREFECT_SESSION_POOL_LIMIT" in source
    assert "$env:PREFECT_DASHBOARD_POOL_LIMIT" in source
    assert "$env:PREFECT_NOTIFY_POOL_LIMIT" in source
    assert lowered.index(reconcile) < lowered.index("-mode worker")
    assert "prefect deploy --all --pool" not in lowered
    assert "prefect deploy --all" in lowered


def test_prefect_worker_command_applies_limit():
    source = (ROOT / "scripts" / "lib" / "prefect_start.ps1").read_text(
        encoding="utf-8"
    )
    assert "[int]$WorkerLimit" in source
    assert '"--limit", $WorkerLimit' in source or "--limit '$WorkerLimit'" in source
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
python -m pytest tests/test_development_environment_contract.py -k "three_bounded_workers or worker_command_applies_limit" -q
```

Expected: failures because only one Worker is started and no limit is passed.

- [ ] **Step 3: Add `WorkerLimit` to `prefect_start.ps1`**

Add:

```powershell
[ValidateRange(1, 128)]
[int]$WorkerLimit = 1
```

Build the Worker command as an argument array to avoid quoting errors:

```powershell
$workerArgs = @("worker", "start", "--pool", $WorkPool, "--type", "process", "--limit", $WorkerLimit)
$startWorker = "& '$PythonExe' -m prefect " + (($workerArgs | ForEach-Object { "'$_'" }) -join " ")
```

Keep the existing 30-second Worker supervisor restart loop.

- [ ] **Step 4: Create or update all three Work Pools in `run.ps1`**

Represent the topology explicitly:

```powershell
$Pools = @(
    [pscustomobject]@{ Name = $env:PREFECT_SESSION_POOL_NAME; Limit = [int]$env:PREFECT_SESSION_POOL_LIMIT },
    [pscustomobject]@{ Name = $env:PREFECT_DASHBOARD_POOL_NAME; Limit = [int]$env:PREFECT_DASHBOARD_POOL_LIMIT },
    [pscustomobject]@{ Name = $env:PREFECT_NOTIFY_POOL_NAME; Limit = [int]$env:PREFECT_NOTIFY_POOL_LIMIT }
)
```

For each Pool, create it when absent and set the Work Pool concurrency limit:

```powershell
& $PythonExe -m prefect work-pool set-concurrency-limit $pool.Name $pool.Limit
if ($LASTEXITCODE -ne 0) {
    throw "设置 Work Pool 并发上限失败: $($pool.Name)"
}
```

- [ ] **Step 5: Reconcile, deploy, and start Workers in the safe order**

After the Server health check:

```powershell
& $PythonExe scripts/lib/prefect_startup_reconcile.py `
    --notify-grace-seconds ([int]$env:AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS)
if ($LASTEXITCODE -ne 0) {
    throw "Prefect 启动队列清理失败，未启动 Worker"
}

& $PythonExe -m prefect deploy --all
```

Do not pass `--pool`; each Deployment already declares its Pool. Start one Worker per Pool with its configured limit, then trigger the initial Session Keeper, then start Web.

- [ ] **Step 6: Make repeated startup reject duplicate managed Workers**

Before starting a Worker supervisor, detect an online Worker for the same Pool through the Prefect API and fail with:

```text
Work Pool 已有在线 Worker，拒绝重复启动: <pool>
```

Task 7 replaces this API-only guard with PID/start-time ownership validation.

- [ ] **Step 7: Run lifecycle tests**

```powershell
python -m pytest tests/test_development_environment_contract.py tests/test_prefect_startup_reconcile.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit three-Worker startup**

```powershell
git add scripts/lib/prefect_start.ps1 scripts/run.ps1 tests/test_development_environment_contract.py
git commit -m "feat: start three bounded Prefect workers"
```

---

### Task 7: Track Project-Owned Processes and Expand Status and Stop Commands

**Files:**
- Create: `scripts/lib/process_registry.ps1`
- Modify: `scripts/lib/prefect_start.ps1`
- Modify: `scripts/lib/start_web.ps1`
- Modify: `scripts/status.ps1`
- Modify: `scripts/stop.ps1`
- Modify: `tests/test_development_environment_contract.py`

**Interfaces:**
- Produces: `Register-ManagedProcess -Name <name> -Process <Process> -Command <string>`.
- Produces: `Get-ManagedProcessRecord -Name <name>`.
- Produces: `Test-ManagedProcessRecord -Record <object> -> bool`.
- Produces: `Stop-ManagedProcessTree -Name <name>`.
- Stores: `C:\AutoNotifyRuntime\processes\<name>.json`.

- [ ] **Step 1: Write failing process ownership tests**

```python
def test_lifecycle_scripts_use_project_process_registry():
    registry = (ROOT / "scripts" / "lib" / "process_registry.ps1").read_text(
        encoding="utf-8"
    )
    run = (ROOT / "scripts" / "run.ps1").read_text(encoding="utf-8")
    status = (ROOT / "scripts" / "status.ps1").read_text(encoding="utf-8")
    stop = (ROOT / "scripts" / "stop.ps1").read_text(encoding="utf-8")

    assert "Register-ManagedProcess" in registry
    assert "process_started_at" in registry
    assert "Test-ManagedProcessRecord" in registry
    assert "process_registry.ps1" in run
    assert "process_registry.ps1" in status
    assert "process_registry.ps1" in stop
    assert "KillAutoNotifyPython" not in stop
```

- [ ] **Step 2: Run the test and verify failure**

```powershell
python -m pytest tests/test_development_environment_contract.py -k "project_process_registry" -q
```

Expected: failure because the registry does not exist and stop still supports broad Python termination.

- [ ] **Step 3: Implement the registry record format**

Each record is written atomically:

```json
{
  "name": "prefect-worker-notify",
  "pid": 1234,
  "process_started_at": "2026-07-12T20:00:00.0000000+08:00",
  "registered_at": "2026-07-12T20:00:01.0000000+08:00",
  "command": "prefect worker start --pool windows-notify-pool --limit 6"
}
```

`Test-ManagedProcessRecord` must require both PID existence and exact process start-time match, preventing Windows PID reuse from validating an unrelated process.

- [ ] **Step 4: Register detached Server, Worker supervisors, backend, and frontend**

Change `Start-Process` calls to use `-PassThru -WindowStyle Hidden` and immediately register the returned process. Use names:

```text
prefect-server
prefect-worker-session
prefect-worker-dashboard
prefect-worker-notify
web-backend
web-frontend
```

Refuse to start a component when its registry record points to the same live process start time.

- [ ] **Step 5: Stop only registered process trees**

Remove the default `KillAutoNotifyPython` behavior. Stop in this order:

```powershell
@(
    "prefect-worker-notify",
    "prefect-worker-dashboard",
    "prefect-worker-session",
    "web-frontend",
    "web-backend",
    "prefect-server"
) | ForEach-Object { Stop-ManagedProcessTree -Name $_ }
```

On Windows, use `taskkill /PID <pid> /T /F` only after the registry PID and start time validate. Remove the registry file after the process tree exits.

- [ ] **Step 6: Expand `status.ps1`**

Print one line per registered process and query Prefect for each Pool. Include:

```text
Prefect API
PostgreSQL
MySQL
windows-session-pool: online workers / running / queued / limit=1
windows-dashboard-pool: online workers / running / queued / limit=4
windows-notify-pool: online workers / running / queued / limit=6
Session state: healthy / verified_at / age_seconds
login.lock: owner PID / held seconds
excel_com.lock: owner PID / held seconds
Disk free: C:\AutoNotifyRuntime
```

Read lock files defensively and print only ownership metadata, never authentication values.

- [ ] **Step 7: Run lifecycle tests**

```powershell
python -m pytest tests/test_development_environment_contract.py -q
```

Expected: all environment and lifecycle contract tests pass.

- [ ] **Step 8: Commit managed lifecycle controls**

```powershell
git add scripts/lib/process_registry.ps1 scripts/lib/prefect_start.ps1 scripts/lib/start_web.ps1 scripts/status.ps1 scripts/stop.ps1 tests/test_development_environment_contract.py
git commit -m "feat: manage project runtime processes safely"
```

---

### Task 8: Document Operations and Run Staged Acceptance

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_GUIDE.md`
- Modify: `scripts/setup_windows_env.ps1`
- Modify: `tests/test_development_environment_contract.py`

**Interfaces:**
- Documents: manual start, status, stop, three Pools, runtime directory, stale-run rules, and recovery behavior.
- Verifies: Notify concurrency transition from `2` during acceptance to final `6`.

- [ ] **Step 1: Write failing documentation contract tests**

```python
def test_docs_describe_three_pool_windows_operations():
    for path in (ROOT / "README.md", ROOT / "PROJECT_GUIDE.md"):
        source = path.read_text(encoding="utf-8")
        for marker in (
            "windows-session-pool",
            "windows-dashboard-pool",
            "windows-notify-pool",
            r"C:\AutoNotifyRuntime",
            "10 分钟",
            "scripts/run.ps1",
            "scripts/status.ps1",
            "scripts/stop.ps1",
        ):
            assert marker in source
```

- [ ] **Step 2: Run the documentation test and verify failure**

```powershell
python -m pytest tests/test_development_environment_contract.py -k "three_pool_windows_operations" -q
```

Expected: failure because the documents describe the previous single-Pool topology.

- [ ] **Step 3: Document the exact operating procedure**

Document:

```powershell
pwsh -File scripts/setup_windows_env.ps1
pwsh -File scripts/run.ps1
pwsh -File scripts/status.ps1
pwsh -File scripts/stop.ps1
```

State explicitly that startup is manual, the dedicated Windows user must remain logged in, and a host reboot requires another `run.ps1` invocation.

- [ ] **Step 4: Document backlog and recovery semantics**

Include:

- Auto-scheduled Notify runs more than ten minutes late are cancelled.
- Manual Notify runs are preserved.
- Expired Session Keeper and high-frequency Dashboard runs do not backfill.
- Running/cancelling/paused managed runs block Worker replacement.
- Worker supervisors restart crashed Workers after thirty seconds.
- A stopped Prefect Server requires manual `run.ps1` recovery.

- [ ] **Step 5: Run the full automated test suite**

```powershell
python -m pytest -p no:cacheprovider -q
```

Expected: all tests pass; tests requiring external production services remain skipped or mocked.

- [ ] **Step 6: Run PowerShell syntax and configuration checks**

```powershell
$files = @(
  "scripts/lib/runtime_config.ps1",
  "scripts/lib/process_registry.ps1",
  "scripts/lib/prefect_start.ps1",
  "scripts/lib/start_web.ps1",
  "scripts/run.ps1",
  "scripts/status.ps1",
  "scripts/stop.ps1"
)
foreach ($file in $files) {
  [void][scriptblock]::Create((Get-Content -Raw -LiteralPath $file))
}
python -c "import yaml; yaml.safe_load(open('prefect.yaml', encoding='utf-8')); print('prefect_yaml_ok')"
```

Expected: no PowerShell parser errors and `prefect_yaml_ok`.

- [ ] **Step 7: Perform isolated startup acceptance with Notify limit two**

On the dedicated Windows host, temporarily set `runtime.work_pools.notify.limit` to `2` in ignored `config/runtime.local.json`, then run:

```powershell
pwsh -File scripts/run.ps1 -SkipWeb
pwsh -File scripts/status.ps1
```

Verify:

- one Server and three online Workers;
- Pool limits `1 / 4 / 2`;
- initial Session Keeper is submitted once;
- stale scheduled runs follow the approved policy;
- a second `run.ps1 -SkipWeb` refuses duplicate managed components;
- `stop.ps1` removes only registered project processes.

- [ ] **Step 8: Perform concurrency and lock acceptance**

Submit three controlled dry-run Notify Flows while the temporary limit is two. Verify two run and one remains scheduled/queued. Drive two test Flows to the Excel boundary and verify `excel_com.lock` permits only one owner. Trigger concurrent Session refresh requests with mocked or controlled authentication failure and verify only one login owner exists.

- [ ] **Step 9: Set the final Notify limit to six and run final status**

Update ignored `config/runtime.local.json` to `runtime.work_pools.notify.limit = 6`, restart the stack, then verify `status.ps1` reports limits `1 / 4 / 6`.

- [ ] **Step 10: Commit operations documentation**

```powershell
git add README.md PROJECT_GUIDE.md scripts/setup_windows_env.ps1 tests/test_development_environment_contract.py
git commit -m "docs: document three-pool Windows operations"
```

---

## Final Verification

Run:

```powershell
python -m pytest -p no:cacheprovider -q
git diff --check
git status --short
```

Expected:

- all tests pass or retain only documented environment-dependent skips;
- no whitespace errors;
- only intentional local ignored configuration and runtime data remain outside Git;
- `scripts/status.ps1` reports one Server, three Workers, Pool limits `1 / 4 / 6`, healthy database connections, and no stale managed locks.
