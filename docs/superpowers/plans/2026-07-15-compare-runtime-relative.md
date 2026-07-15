# Compare Runtime-Relative Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve Flow-generated root-relative compare inputs below the shared runtime root and document the remaining legacy compatibility callers.

**Architecture:** `compare_service.resolve_path()` first rejects the removed `runtime/...` prefix, then resolves allow-listed root-relative paths through `resolve_runtime_relative_path()`. Absolute paths and project-owned relative paths retain their existing behavior. A versioned audit document prevents deleting `resolve_runtime_path()` while scripts still call it.

**Tech Stack:** Python 3.13, pytest, Markdown.

## Global Constraints

- Do not run login, downloads, browser control, Prefect, or notifications.
- Do not alter the two dashboard tool scripts in this task; record them as pending migration.
- Root-relative runtime paths must be allow-listed by `validate_runtime_relative_path()`.
- `runtime/...` must raise a `ValueError`; it must never fall back to the repository directory.

---

### Task 1: Resolve compare-service root-relative inputs

**Files:**
- Modify: `tests/test_compare_service.py`
- Modify: `services/compare_service.py:17-45`

**Interfaces:**
- Consumes: `resolve_path(value, base_dir=PROJECT_DIR) -> Path | None`
- Produces: shared-root resolution for `flow/...`, project-root resolution for `templates/...`, and a clear error for `runtime/...`.

- [ ] **Step 1: Write failing tests**

```python
import pytest

def test_resolve_path_rebases_root_relative_flow_paths(monkeypatch, tmp_path):
    runtime_root = tmp_path / "shared-runtime"
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(runtime_root))
    assert resolve_path("flow/日报/tmp/result.json") == (
        runtime_root / "flow" / "日报" / "tmp" / "result.json"
    ).resolve()

def test_resolve_path_keeps_project_relative_templates(tmp_path):
    assert resolve_path("templates/日报.xlsx", base_dir=tmp_path) == (
        tmp_path / "templates" / "日报.xlsx"
    ).resolve()

def test_resolve_path_rejects_legacy_runtime_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared-runtime"))
    with pytest.raises(ValueError, match="不得以 runtime/"):
        resolve_path("runtime/flow/日报/tmp/result.json")
```

- [ ] **Step 2: Run the new tests and observe the failure**

Run: `python -m pytest -p no:cacheprovider tests/test_compare_service.py -q`

Expected: the flow-path test resolves below the repository and the legacy-prefix test does not raise.

- [ ] **Step 3: Implement the minimal resolver split**

```python
from services.runtime_paths import (
    is_runtime_relative_path,
    resolve_runtime_relative_path,
)

path = Path(value)
if path.parts and path.parts[0].lower() == "runtime":
    raise ValueError(f"运行根相对路径不得以 runtime/ 开头: {value}")
if is_runtime_relative_path(path):
    return resolve_runtime_relative_path(path)
if path.is_absolute():
    return path
return (base_dir / path).resolve()
```

- [ ] **Step 4: Run focused verification**

Run: `python -m pytest -p no:cacheprovider tests/test_compare_service.py tests/test_flow_helpers.py -q`

Expected: PASS.

### Task 2: Record compatibility-layer retirement blockers

**Files:**
- Create: `docs/runtime-path-compatibility-audit.md`
- Test: `tests/test_runtime_paths.py`

**Interfaces:**
- Consumes: the production `resolve_runtime_path()` callers and the legacy-prefix test contract.
- Produces: an auditable status table for every legacy compatibility boundary.

- [ ] **Step 1: Write a failing audit-presence test**

```python
def test_runtime_compatibility_audit_records_remaining_callers():
    project_root = Path(__file__).resolve().parents[1]
    audit = (project_root / "docs" / "runtime-path-compatibility-audit.md").read_text(encoding="utf-8")
    assert "services/runtime_paths.py" in audit
    assert "scripts/tools/dashboard/import_v2_indicator_config.py" in audit
    assert "scripts/tools/dashboard/v2_cutover_audit.py" in audit
    assert "待迁移" in audit
```

- [ ] **Step 2: Run the test and observe the missing-file failure**

Run: `python -m pytest -p no:cacheprovider tests/test_runtime_paths.py::test_runtime_compatibility_audit_records_remaining_callers -q`

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Create the audit document**

```markdown
# 运行路径兼容层审计

| 位置 | 状态 | 下线条件 |
| --- | --- | --- |
| `services/runtime_paths.py:resolve_runtime_path` | 保留为拒绝旧格式 | 所有调用者迁移后再删除 |
| `services/compare_service.py` | 已迁移 | 根相对、项目相对和旧前缀测试通过 |
| `scripts/tools/dashboard/import_v2_indicator_config.py` | 待迁移 | 参数改用 `resolve_runtime_relative_path()` |
| `scripts/tools/dashboard/v2_cutover_audit.py` | 待迁移 | `--bundle` 与 `--approvals` 改用根相对解析 |
```

- [ ] **Step 4: Run focused verification**

Run: `python -m pytest -p no:cacheprovider tests/test_runtime_paths.py tests/test_compare_service.py tests/test_flow_helpers.py -q`

Expected: PASS.

### Task 3: Final verification

**Files:**
- Test: all affected files

- [ ] **Step 1: Run the complete Python suite**

Run: `python -m pytest -p no:cacheprovider -q`

Expected: PASS with no failures.

- [ ] **Step 2: Check whitespace and inspect unresolved compatibility callers**

Run: `git diff --check`

Run: `rg -n 'resolve_runtime_path\\(' services scripts`

Expected: no whitespace errors; the audit document lists every remaining production caller.
