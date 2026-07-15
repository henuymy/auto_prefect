# Runtime-Root Relative Regression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent runtime-root paths from regressing to the removed `runtime/...` prefix across path resolution, config APIs, Flow generation, and frontend display.

**Architecture:** Python tests assert that every runtime artifact is represented by an allow-listed path relative to `runtime.root`, then resolves below the configured shared root. The frontend keeps the API-provided relative path unchanged; TypeScript compilation verifies the presentation change.

**Tech Stack:** Python 3.13, pytest, TypeScript, React, Vite.

## Global Constraints

- Do not execute login, download, browser, Prefect, or notification workflows.
- Do not alter runtime roots, credentials, or deployment configuration.
- Runtime values use `session/...`, `config/...`, `logs/...`, `health/...`, `starter_templates/...`, `temp/...`, `modules/<name>/output/...`, or `flow/<task>/{output,backup,debug,tmp}/...`.
- Reject absolute paths, parent-directory traversal, and the legacy `runtime/...` prefix.

---

### Task 1: Lock the path and API contract

**Files:**
- Modify: `tests/test_runtime_paths.py`
- Modify: `tests/test_config_store.py`

**Interfaces:**
- Consumes: `services.runtime_paths.resolve_runtime_relative_path(value) -> Path`
- Produces: regression coverage for root-relative input, legacy-prefix rejection, and config-store responses.

- [ ] **Step 1: Write failing tests**

```python
def test_root_relative_resolver_rejects_absolute_and_legacy_values(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))
    with pytest.raises(ValueError):
        resolve_runtime_relative_path(tmp_path / "outside.json")
    with pytest.raises(ValueError):
        resolve_runtime_relative_path("runtime/session/cookie_dump.json")

def test_list_versions_returns_root_relative_paths(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    _write(config_store.VERSIONS_DIR / "日报" / "1.json", "日报")
    assert config_store.list_versions("日报")[0]["path"] == "config/versions/日报/1.json"
```

- [ ] **Step 2: Run the focused regression tests**

Run: `python -m pytest -p no:cacheprovider tests/test_runtime_paths.py tests/test_config_store.py -q`

Expected: PASS. The path implementation was migrated before this plan; these tests lock
the existing behavior instead of requiring a production change.

- [ ] **Step 3: Correct production code only if a regression is exposed**

```python
versions.append({"path": f"config/versions/{name}/{path.name}", ...})
```

Do not change production code when the new assertions pass. If they fail, preserve this
root-relative version-path shape and keep `resolve_runtime_relative_path()` as the only
runtime-value resolver used by the tests.

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest -p no:cacheprovider tests/test_runtime_paths.py tests/test_config_store.py -q`

Expected: PASS.

### Task 2: Lock Flow-generated artifact paths

**Files:**
- Modify: `tests/test_flow_helpers.py`
- Modify: `flows/notify_single_flow.py` only if the new tests reveal a root escape or legacy prefix.

**Interfaces:**
- Consumes: `materialize_runtime_paths(config, flow_runtime_dir)` and `flow_runtime_path(flow_runtime_dir, area, *parts)`.
- Produces: generated step fields rooted at `flow/<task>/...`.

- [ ] **Step 1: Write the failing test**

```python
def test_materialized_flow_paths_are_root_relative_and_resolvable(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))
    config, _ = load_config("config/tasks/PK赛通报.json")
    materialize_runtime_paths(config, Path("flow/PK赛通报"))
    output = config["steps"]["update_template"]["output_dir"]
    assert output == "flow/PK赛通报/output/templates"
    assert resolve_runtime_relative_path(output).is_relative_to(tmp_path / "shared")
```

- [ ] **Step 2: Run the Flow regression test**

Run: `python -m pytest -p no:cacheprovider tests/test_flow_helpers.py::test_materialized_flow_paths_are_root_relative_and_resolvable -q`

Expected: PASS. The test documents that generated Flow values remain root-relative and
resolve beneath the configured shared root.

- [ ] **Step 3: Apply the minimal Flow correction if required**

```python
flow_runtime_dir = Path(config.get("runtime_dir", f"flow/{resolved_config_path.stem}"))
validate_runtime_relative_path(flow_runtime_path(flow_runtime_dir, "output"))
```

- [ ] **Step 4: Run Flow regression tests**

Run: `python -m pytest -p no:cacheprovider tests/test_flow_helpers.py tests/test_notify_flow_session.py -q`

Expected: PASS.

### Task 3: Verify frontend presentation and final regression suite

**Files:**
- Modify: `frontend/src/components/runtime/RuntimeDrawer.tsx` only if type checking reveals an error.
- Modify: `frontend/src/lib/api.ts` only if the cleanup log regresses to a legacy prefix.

**Interfaces:**
- Consumes: `RuntimeDrawer` prop `currentPath: string`.
- Produces: `.` for the runtime root and the unmodified root-relative child path otherwise.

- [ ] **Step 1: Add a source-level assertion to the existing runtime-path test suite**

```python
def test_runtime_drawer_does_not_prepend_legacy_runtime_prefix():
    project_root = Path(__file__).resolve().parents[1]
    source = (project_root / "frontend/src/components/runtime/RuntimeDrawer.tsx").read_text(encoding="utf-8")
    assert "runtime/{props.currentPath}" not in source
    assert 'props.currentPath || "."' in source
```

- [ ] **Step 2: Run the new test**

Run: `python -m pytest -p no:cacheprovider tests/test_runtime_paths.py -q`

Expected: PASS after the existing drawer change is confirmed.

- [ ] **Step 3: Run TypeScript type checking when dependencies exist**

Run: `npm run typecheck`

Working directory: `frontend`

Expected: PASS. If `frontend/node_modules` is absent, record that the check could not run; do not install dependencies or change lock files.

- [ ] **Step 4: Run final Python verification**

Run: `python -m pytest -p no:cacheprovider -q`

Expected: PASS with no failures.

- [ ] **Step 5: Check the patch**

Run: `git diff --check`

Expected: exit code `0`.
