# Quality Gate and Capture Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a passing quality gate and make the only effective screenshot-resolution setting explicit and tested.

**Architecture:** Keep the Excel-to-PDF-to-PNG rendering path. Extract DPI normalization into a pure function, retain legacy configuration compatibility by ignoring old fields, and test the renderer-facing value directly. Add frontend validation to the existing Python CI job while preserving its MySQL and lock checks.

**Tech Stack:** Python, Pytest, Ruff, SQLAlchemy, PyMuPDF, GitHub Actions, Node.js 20, TypeScript, Vite.

## Global Constraints

- `pdf_dpi` remains the only screenshot-resolution setting and is clamped to the inclusive range 96–600.
- Existing configurations containing `appearance`, `format`, or `export_scale` must still run without error; those keys have no rendering effect.
- The local Webhook value must not be copied into tracked files, documentation, test output, or commits.
- The CI job retains the existing MySQL service, Python lock verification, Ruff invocation, and Pytest invocation.
- This batch makes no Enterprise WeCom network call and requires no Excel COM test.

---

### Task 1: Restore the Python static-analysis gate

**Files:**

- Modify: `services/session_alert_service.py:151-167`
- Modify: `tests/test_business_run_alert_service.py:28`
- Modify: `tests/test_dashboard_metric_flow_session.py:4`
- Modify: `tests/test_session_manager.py:7`

**Interfaces:**

- Consumes: Ruff rule set declared by `pyproject.toml`.
- Produces: a clean result from `ruff check backend infrastructure models services tasks flows tests migrations`.

- [ ] **Step 1: Confirm the failing static-analysis gate**

Run: `ruff check backend infrastructure models services tasks flows tests migrations`

Expected: five errors, including a duplicate `failure_category`, one E731 lambda assignment, and three F401 unused imports.

- [ ] **Step 2: Apply the minimal lint corrections**

In `notify_session_failure`, keep exactly one persisted failure category:

```python
"failure_category": _redact(incident.get("failure_category")),
```

Delete the earlier `incident["failure_category"]` entry. Remove `SessionInfrastructureError` and `SessionLoginError` from `tests/test_dashboard_metric_flow_session.py`, and remove `Mock` from `tests/test_session_manager.py`.

Replace the named lambda in `test_business_run_alert_sends_once_per_development_flow_run` with:

```python
def sender(_url, message, timeout=30):
    messages.append(message)
    return {"errcode": 0}
```

- [ ] **Step 3: Verify and commit**

Run: `ruff check backend infrastructure models services tasks flows tests migrations`

Expected: `All checks passed!`

```powershell
git add services/session_alert_service.py tests/test_business_run_alert_service.py tests/test_dashboard_metric_flow_session.py tests/test_session_manager.py
git commit -m "fix: restore ruff quality gate"
```

### Task 2: Make screenshot DPI normalization explicit and tested

**Files:**

- Modify: `services/screenshot_service.py:481-489`
- Modify: `tests/test_screenshot_service.py:6-11`
- Modify: `config/modules/wecom_sender.local.json:2-8` (ignored local file; never stage)

**Interfaces:**

- Produces: `resolve_pdf_dpi(capture: dict) -> int`.
- Guarantees: 300 when unset, 96–600 clamping, and no effect from `export_scale`.

- [ ] **Step 1: Add a failing resolver test**

Import `resolve_pdf_dpi` and add:

```python
@pytest.mark.parametrize(
    ("capture", "expected"),
    [
        ({}, 300),
        ({"pdf_dpi": 600}, 600),
        ({"pdf_dpi": 1}, 96),
        ({"pdf_dpi": 999}, 600),
        ({"pdf_dpi": 300, "export_scale": 1}, 300),
        ({"pdf_dpi": 300, "export_scale": 2}, 300),
    ],
)
def test_resolve_pdf_dpi_uses_only_pdf_dpi(capture, expected):
    assert resolve_pdf_dpi(capture) == expected
```

- [ ] **Step 2: Run the focused test before implementation**

Run: `python -m pytest tests/test_screenshot_service.py::test_resolve_pdf_dpi_uses_only_pdf_dpi -q`

Expected: import failure because `resolve_pdf_dpi` does not yet exist.

- [ ] **Step 3: Implement the pure resolver and delegate rendering to it**

Add immediately before `render_pdf_to_png`:

```python
def resolve_pdf_dpi(capture):
    configured = capture.get("pdf_dpi", capture.get("render_dpi", 300))
    return max(96, min(600, int(configured or 300)))
```

Replace the current two-line DPI calculation in `render_pdf_to_png` with:

```python
dpi = resolve_pdf_dpi(capture)
```

- [ ] **Step 4: Remove only ineffective local fields**

Keep the local capture override as:

```json
"capture_defaults": {
  "pdf_dpi": 600,
  "optimize_png": true
}
```

Do not stage this ignored file and do not print its Webhook value.

- [ ] **Step 5: Verify and commit tracked changes**

Run: `python -m pytest tests/test_screenshot_service.py -q`

Expected: all screenshot tests pass, including the six DPI cases.

```powershell
git add services/screenshot_service.py tests/test_screenshot_service.py
git commit -m "fix: clarify screenshot dpi configuration"
```

### Task 3: Give Dashboard V2 timestamps an SQLite-compatible SQLAlchemy variant

**Files:**

- Modify: `models/dashboard_v2.py:7-36,80-540`
- Test: `tests/test_dashboard_v2_query_service.py`
- Test: `tests/test_dashboard_v2_schema.py`

**Interfaces:**

- Produces: `DATETIME_MS`, a SQLAlchemy type that remains `mysql.DATETIME(fsp=3)` on MySQL and uses generic `DateTime()` on SQLite.
- Guarantees: SQLAlchemy serializes Python `datetime` values before SQLite binding while preserving MySQL millisecond DDL.

- [ ] **Step 1: Verify the adapter failure**

Run: `python -m pytest tests/test_dashboard_v2_query_service.py -q -W error::DeprecationWarning`

Expected: failures containing `The default datetime adapter is deprecated`.

- [ ] **Step 2: Add a cross-dialect timestamp type**

Import `DateTime` from `sqlalchemy`, then define:

```python
DATETIME_MS = mysql.DATETIME(fsp=3).with_variant(DateTime(), "sqlite")
```

Use `DATETIME_MS` in `_created_at_column`, `_updated_at_column`, and every declaration now using `mysql.DATETIME(fsp=3)`: `CollectionRunV2.started_at`, `finished_at`; `HierarchyNode.last_seen_at`; `HierarchyParentHistory.valid_from`, `valid_to`; `IndicatorV2.removed_at`; `TargetPlan.activated_at`, `retired_at`; `MetricCurrentV2.collected_at`; `MetricSnapshotV2.collected_at`; and `MetricAccV2.collected_at`.

- [ ] **Step 3: Verify and commit**

Run: `python -m pytest tests/test_dashboard_v2_schema.py tests/test_dashboard_v2_query_service.py -q -W error::DeprecationWarning`

Expected: all selected tests pass without SQLite datetime-adapter failures.

```powershell
git add models/dashboard_v2.py tests/test_dashboard_v2_query_service.py tests/test_dashboard_v2_schema.py
git commit -m "fix: support sqlite timestamp bindings"
```

### Task 4: Add frontend verification to CI and run the complete gate

**Files:**

- Modify: `.github/workflows/ci.yml:30-45`

**Interfaces:**

- Consumes: `frontend/package-lock.json` and scripts in `frontend/package.json`.
- Produces: a CI job that blocks on Python lint/tests and frontend dependency, type, and production-build failures.

- [ ] **Step 1: Add deterministic frontend CI commands**

After the existing Pytest step, add:

```yaml
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: Verify frontend
        working-directory: frontend
        run: |
          npm ci
          npm run typecheck
          npm run build
```

- [ ] **Step 2: Run the frontend equivalent locally**

Working directory: `frontend`

Run: `npm ci; npm run typecheck; npm run build`

Expected: deterministic install, no TypeScript errors, and Vite prints `built in`.

- [ ] **Step 3: Run the complete local quality gate**

```powershell
ruff check backend infrastructure models services tasks flows tests migrations
python -m pytest -q -W error::DeprecationWarning
```

Expected: Ruff and every test pass; no SQLite datetime-adapter warning appears.

- [ ] **Step 4: Review and commit**

Run: `git diff --check; git status --short`

Expected: no whitespace error; the ignored local configuration and unrelated untracked plan stay unstaged.

```powershell
git add .github/workflows/ci.yml
git commit -m "ci: verify frontend build"
```

