# Runtime Path Compatibility Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the legacy `runtime/...` path compatibility layer after moving the two remaining Dashboard maintenance-script inputs to controlled `runtime.root`-relative paths.

**Architecture:** The two script argument boundaries will parse runtime artifacts through `resolve_runtime_relative_path()`. `validate_runtime_relative_path()` will contain the allow-list directly, and `runtime_path()` will resolve only validated path parts below the shared runtime root. The retired legacy APIs will have no production callers or test imports.

**Tech Stack:** Python 3.11+, `pathlib`, `argparse`, `pytest`, Markdown.

## Global Constraints

- Do not change login, download, notification, database-import, Prefect, or Dashboard business logic.
- `--bundle` and `--approvals` are runtime-root-relative arguments; `--config` remains a project configuration path.
- Runtime arguments must reject empty values, absolute paths, `..`, and the historical `runtime/...` prefix.
- Preserve the existing runtime layout allow-list: `session/`, `config/{drafts,versions}/`, `logs/`, `health/`, `starter_templates/`, `temp/`, `modules/<module>/output/`, and `flow/<task>/{output,backup,debug,tmp}/`.
- Do not set external environment variables or execute real login, download, notification, database import, or Prefect actions.

---

### Task 1: Move Dashboard maintenance-script artifacts to the root-relative resolver

**Files:**
- Modify: `scripts/tools/dashboard/import_v2_indicator_config.py:16-18,171-185`
- Modify: `scripts/tools/dashboard/v2_cutover_audit.py:31,255-303`
- Modify: `tests/test_dashboard_v2_indicator_config_import.py`
- Modify: `tests/test_dashboard_v2_cutover_audit.py`

**Interfaces:**
- Consumes: `resolve_runtime_relative_path(value: str | Path) -> Path`.
- Produces: `resolve_bundle_path(value: str | Path) -> Path` and `parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace` in the import script; `resolve_runtime_artifact_path(value: str | Path) -> Path` and an argv-aware `parse_args()` in the cutover script.

- [ ] **Step 1: Write the failing script-boundary tests**

```python
# tests/test_dashboard_v2_indicator_config_import.py
import pytest

def test_indicator_import_bundle_uses_runtime_root(monkeypatch, tmp_path):
    runtime_root = tmp_path / "shared-runtime"
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(runtime_root))
    args = import_v2_indicator_config.parse_args([])

    assert args.bundle == "modules/dashboard/output/v2_migration"
    assert import_v2_indicator_config.resolve_bundle_path(args.bundle) == (
        runtime_root / "modules/dashboard/output/v2_migration"
    ).resolve()

@pytest.mark.parametrize("value", [
    "runtime/modules/dashboard/output/v2_migration",
    "../outside",
    "C:/outside",
])
def test_indicator_import_rejects_non_runtime_relative_bundle(value):
    with pytest.raises(ValueError):
        import_v2_indicator_config.resolve_bundle_path(value)
```

```python
# tests/test_dashboard_v2_cutover_audit.py
import pytest
from scripts.tools.dashboard import v2_cutover_audit

def test_cutover_audit_runtime_inputs_use_runtime_root(monkeypatch, tmp_path):
    runtime_root = tmp_path / "shared-runtime"
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(runtime_root))
    args = v2_cutover_audit.parse_args([])

    assert v2_cutover_audit.resolve_runtime_artifact_path(args.bundle) == (
        runtime_root / "modules/dashboard/output/v2_migration"
    ).resolve()
    assert v2_cutover_audit.resolve_runtime_artifact_path(args.approvals) == (
        runtime_root / "modules/dashboard/output/v2_migration/cutover_approvals.json"
    ).resolve()

@pytest.mark.parametrize("value", [
    "runtime/modules/dashboard/output/v2_migration",
    "../outside",
    "C:/outside",
])
def test_cutover_audit_rejects_non_runtime_relative_artifact(value):
    with pytest.raises(ValueError):
        v2_cutover_audit.resolve_runtime_artifact_path(value)
```

- [ ] **Step 2: Run the new tests and verify they fail because the boundaries do not exist**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_dashboard_v2_indicator_config_import.py tests/test_dashboard_v2_cutover_audit.py -q
```

Expected: FAIL with missing `parse_args`, `resolve_bundle_path`, or `resolve_runtime_artifact_path` attributes. No database, Prefect, or network action is invoked by these tests.

- [ ] **Step 3: Implement pure root-relative argument boundaries**

```python
# import_v2_indicator_config.py
from typing import Any, Sequence
from services.runtime_paths import resolve_runtime_relative_path

def resolve_bundle_path(value: str | Path) -> Path:
    return resolve_runtime_relative_path(value)

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入驾驶舱 V2 指标设置和自建公式")
    parser.add_argument("--bundle", default="modules/dashboard/output/v2_migration")
    parser.add_argument("--expected-database", default="dashboard_v2")
    return parser.parse_args(argv)

def main() -> int:
    args = parse_args()
    bundle = resolve_bundle_path(args.bundle)
```

```python
# v2_cutover_audit.py
from typing import Any, Sequence
from services.runtime_paths import resolve_runtime_relative_path

def resolve_runtime_artifact_path(value: str | Path) -> Path:
    return resolve_runtime_relative_path(value)

async def audit(args: argparse.Namespace) -> dict[str, Any]:
    bundle = resolve_runtime_artifact_path(args.bundle)
    approvals = resolve_runtime_artifact_path(args.approvals)
    checks = {
        "migration_bundle": _bundle_check(
            bundle,
            require_pk=args.require_pk_targets,
        ),
        "approval_evidence": _approval_check(approvals),
    }

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读审计驾驶舱 V2 切换门槛")
    parser.add_argument("--phase", choices=["cutover"], default="cutover")
    parser.add_argument("--expected-database", default="dashboard_v2")
    parser.add_argument("--config", default="config/dashboard/session.json")
    parser.add_argument("--bundle", default="modules/dashboard/output/v2_migration")
    parser.add_argument(
        "--approvals",
        default="modules/dashboard/output/v2_migration/cutover_approvals.json",
    )
    parser.add_argument("--prefect-api-url", default="http://127.0.0.1:4200/api")
    parser.add_argument("--require-pk-targets", action="store_true")
    return parser.parse_args(argv)
```

Remove both `resolve_runtime_path` imports and do not pass `project_dir` for these runtime artifacts.

- [ ] **Step 4: Run the focused script tests**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_dashboard_v2_indicator_config_import.py tests/test_dashboard_v2_cutover_audit.py -q
```

Expected: PASS. The tests invoke only argument parsing and pure path resolution.

- [ ] **Step 5: Commit the script-boundary migration**

```powershell
git add scripts/tools/dashboard/import_v2_indicator_config.py scripts/tools/dashboard/v2_cutover_audit.py tests/test_dashboard_v2_indicator_config_import.py tests/test_dashboard_v2_cutover_audit.py
git commit -m "refactor: resolve dashboard artifacts from runtime root"
```

### Task 2: Delete the old resolver and make root-relative validation self-contained

**Files:**
- Modify: `services/runtime_paths.py:50-126`
- Modify: `tests/test_runtime_paths.py`

**Interfaces:**
- Consumes: a string or `Path` representing a logical path below `runtime_root()`.
- Produces: `validate_runtime_relative_path(value: str | Path) -> Path`, `runtime_path(relative: str | Path) -> Path`, `resolve_runtime_relative_path(value: str | Path) -> Path`, and `is_runtime_relative_path(value: str | Path | None) -> bool`.
- Removes: `validate_runtime_path()` and `resolve_runtime_path()`.

- [ ] **Step 1: Replace legacy resolver tests with the retirement contract**

```python
# tests/test_runtime_paths.py
from services.runtime_paths import (
    display_path,
    resolve_runtime_relative_path,
    runtime_path,
    runtime_root,
)

@pytest.mark.parametrize("logical", [
    "session/cookie_dump.json",
    "config/drafts/日报.json",
    "config/versions/日报/1.json",
    "logs/web_runs.jsonl",
    "modules/dashboard/output/v2_migration",
    "flow/日报/tmp/result.json",
])
def test_root_relative_allow_list_rebases_to_shared_root(monkeypatch, tmp_path, logical):
    runtime_dir = tmp_path / "shared"
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(runtime_dir))

    assert resolve_runtime_relative_path(logical) == (runtime_dir / logical).resolve()

@pytest.mark.parametrize("value", [
    "",
    ".",
    "./",
    "runtime/session/cookie_dump.json",
    "../outside",
    "C:/outside",
])
def test_root_relative_paths_reject_uncontrolled_values(value):
    with pytest.raises(ValueError):
        resolve_runtime_relative_path(value)

def test_legacy_runtime_resolver_apis_are_removed():
    assert not hasattr(runtime_paths, "validate_runtime_path")
    assert not hasattr(runtime_paths, "resolve_runtime_path")
```

Delete every test that imports or calls `resolve_runtime_path()`, including tests for the legacy `runtime/...` prefix, project-relative fallback, absolute fallback, and legacy configuration/operational paths.

- [ ] **Step 2: Run the runtime-path tests and verify that the legacy API assertion fails**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_runtime_paths.py -q
```

Expected: FAIL because `validate_runtime_path` and `resolve_runtime_path` still exist. Existing root-relative checks remain green.

- [ ] **Step 3: Make root-relative validation the only implementation**

```python
def validate_runtime_relative_path(value: str | Path) -> Path:
    raw = str(value).replace("\\", "/")
    path = Path(raw)
    if not raw or not path.parts:
        raise ValueError(f"运行根相对路径不能为空: {value}")
    if path.parts and path.parts[0].lower() == "runtime":
        raise ValueError(f"运行根相对路径不得以 runtime/ 开头: {value}")
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"运行根相对路径必须是受控的相对路径: {value}")

    parts = path.parts
    allowed = (
        parts[0] == "session"
        or (len(parts) >= 2 and parts[0] == "config" and parts[1] in _CONFIG_AREAS)
        or parts[0] in _OPERATIONAL_AREAS
        or (len(parts) >= 3 and parts[0] == "modules" and parts[2] == "output")
        or (len(parts) >= 3 and parts[0] == "flow" and parts[2] in _FLOW_AREAS)
    )
    if not allowed:
        raise ValueError(f"不允许未分类的运行根相对路径: {value}")
    return Path(*parts)

def runtime_path(relative: str | Path) -> Path:
    logical_path = validate_runtime_relative_path(relative)
    root = runtime_root()
    resolved = (root / logical_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"运行根相对路径解析后越出运行根: {relative}") from exc
    return resolved
```

Delete the entire `validate_runtime_path()` and `resolve_runtime_path()` definitions. Preserve `display_path()` and the root-relative public helpers.

- [ ] **Step 4: Run runtime and dependent consumer tests**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_runtime_paths.py tests/test_compare_service.py tests/test_flow_helpers.py tests/test_config_store.py -q
```

Expected: PASS. The compare, Flow, and configuration consumers still resolve allowed root-relative paths and reject the old prefix.

- [ ] **Step 5: Commit the compatibility API deletion**

```powershell
git add services/runtime_paths.py tests/test_runtime_paths.py
git commit -m "refactor: remove legacy runtime path resolver"
```

### Task 3: Close the audit and run the regression baseline

**Files:**
- Modify: `docs/runtime-path-compatibility-audit.md`
- Modify: `tests/test_runtime_paths.py`

**Interfaces:**
- Consumes: the post-migration source tree.
- Produces: an audit document that records compatibility retirement, the exact zero-caller search, and the retained root-relative rejection contract.

- [ ] **Step 1: Change the audit regression test to require completed retirement**

```python
def test_runtime_compatibility_audit_records_retirement():
    project_root = Path(__file__).resolve().parents[1]
    audit = (project_root / "docs" / "runtime-path-compatibility-audit.md").read_text(
        encoding="utf-8"
    )

    assert "已下线" in audit
    assert "待迁移" not in audit
    assert "resolve_runtime_relative_path()" in audit
    assert "rg -n 'resolve_runtime_path\\(|validate_runtime_path\\('" in audit
```

- [ ] **Step 2: Run that test and verify it fails against the pending-migration audit**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_runtime_paths.py::test_runtime_compatibility_audit_records_retirement -q
```

Expected: FAIL because the document currently contains `待迁移` and does not record retirement.

- [ ] **Step 3: Update the audit to the completed state**

Replace its pending-caller table with:

````markdown
# 运行路径兼容层审计（已下线）

旧 `runtime/...` 解析函数已删除。运行目录参数只接受相对于 `runtime.root` 的
白名单路径；`runtime/...`、绝对路径和包含 `..` 的路径会被拒绝。

| 原边界 | 状态 | 现行契约 |
| --- | --- | --- |
| `services/runtime_paths.py` 旧解析 API | 已下线 | 使用 `resolve_runtime_relative_path()` |
| Dashboard V2 导入脚本 `--bundle` | 已迁移 | `modules/<module>/output/...` |
| Dashboard V2 切换审计 `--bundle`、`--approvals` | 已迁移 | `modules/<module>/output/...` |

复核命令：

```powershell
rg -n 'resolve_runtime_path\\(|validate_runtime_path\\(' services scripts tests
python -m pytest -p no:cacheprovider tests/test_runtime_paths.py tests/test_dashboard_v2_indicator_config_import.py tests/test_dashboard_v2_cutover_audit.py -q
```
````

- [ ] **Step 4: Run static and focused verification**

Run:

```powershell
rg -n 'resolve_runtime_path\\(|validate_runtime_path\\(' services scripts tests
python -m pytest -p no:cacheprovider tests/test_runtime_paths.py tests/test_dashboard_v2_indicator_config_import.py tests/test_dashboard_v2_cutover_audit.py tests/test_compare_service.py tests/test_flow_helpers.py tests/test_config_store.py -q
git diff --check
```

Expected: the `rg` command has no matches (exit code 1 is expected); pytest passes; `git diff --check` produces no output.

- [ ] **Step 5: Run the complete Python baseline and commit the audit closure**

Run:

```powershell
python -m pytest -p no:cacheprovider -q
git add docs/runtime-path-compatibility-audit.md tests/test_runtime_paths.py
git commit -m "docs: close runtime path compatibility audit"
```

Expected: the complete suite passes without triggering external workflows, then the audit closure is committed.
