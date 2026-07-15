import os
import subprocess
import sys
from pathlib import Path

import pytest

from services import runtime_paths
from services.runtime_paths import (
    display_path,
    resolve_runtime_relative_path,
    runtime_path,
    runtime_root,
)


@pytest.mark.parametrize(
    "logical_path",
    [
        "session/cookie_dump.json",
        "config/drafts/日报.json",
        "config/versions/日报/1.json",
        "logs/web_runs.jsonl",
        "health/probe",
        "starter_templates/日报/run",
        "temp/working.json",
        "modules/dashboard/output/v2_migration",
        "flow/日报/tmp/result.json",
    ],
)
def test_root_relative_allow_list_rebases_to_external_root(
    monkeypatch, tmp_path, logical_path
):
    runtime_root = tmp_path / "shared"
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(runtime_root))

    assert resolve_runtime_relative_path(logical_path) == (
        runtime_root / logical_path
    ).resolve()


def test_root_relative_runtime_paths_reject_legacy_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))

    with pytest.raises(ValueError, match="不得以 runtime/"):
        resolve_runtime_relative_path("runtime/session/cookie_dump.json")


def test_root_relative_runtime_paths_reject_absolute_values(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))

    with pytest.raises(ValueError, match="受控的相对路径"):
        resolve_runtime_relative_path(tmp_path / "outside.json")


@pytest.mark.parametrize("value", ["", ".", "./"])
def test_root_relative_runtime_paths_reject_empty_values(value):
    with pytest.raises(ValueError):
        resolve_runtime_relative_path(value)


def test_legacy_runtime_resolver_apis_are_removed():
    assert not hasattr(runtime_paths, "validate_runtime_path")
    assert not hasattr(runtime_paths, "resolve_runtime_path")


def test_runtime_root_uses_local_runtime_config_when_environment_is_unset(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    runtime_dir = tmp_path / "configured-runtime"
    config_path = project_dir / "config" / "runtime.local.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '{"runtime": {"root": "' + runtime_dir.as_posix() + '"}}',
        encoding="utf-8",
    )
    monkeypatch.delenv("AUTO_NOTIFY_RUNTIME_ROOT", raising=False)
    monkeypatch.setattr(runtime_paths, "PROJECT_DIR", project_dir)

    assert runtime_paths.runtime_root() == runtime_dir.resolve()


def test_runtime_root_rejects_relative_local_config(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    config_path = project_dir / "config" / "runtime.local.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"runtime": {"root": "shared-runtime"}}', encoding="utf-8")
    monkeypatch.delenv("AUTO_NOTIFY_RUNTIME_ROOT", raising=False)
    monkeypatch.setattr(runtime_paths, "PROJECT_DIR", project_dir)

    with pytest.raises(ValueError, match="必须是绝对路径"):
        runtime_paths.runtime_root()


def test_runtime_root_uses_machine_shared_default_without_override(monkeypatch):
    monkeypatch.delenv("AUTO_NOTIFY_RUNTIME_ROOT", raising=False)

    assert runtime_root() == Path(r"C:\AutoNotifyRuntime").resolve()


def test_runtime_root_direct_launch_is_independent_of_working_directory(
    monkeypatch, tmp_path
):
    project_dir = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(project_dir)}
    env.pop("AUTO_NOTIFY_RUNTIME_ROOT", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from services.runtime_paths import runtime_root; print(runtime_root())",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )

    assert Path(completed.stdout.strip()) == Path(r"C:\AutoNotifyRuntime").resolve()


def test_runtime_path_is_resolved_lazily(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "first"))
    assert runtime_path("session/locks/excel.lock") == (
        tmp_path / "first" / "session" / "locks" / "excel.lock"
    ).resolve()

    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "second"))
    assert runtime_path("session/locks/excel.lock") == (
        tmp_path / "second" / "session" / "locks" / "excel.lock"
    ).resolve()


def test_runtime_path_rejects_escape_and_legacy_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))

    with pytest.raises(ValueError, match="受控"):
        runtime_path("../outside")
    with pytest.raises(ValueError, match="未分类"):
        runtime_path("dashboard/failures")


def test_display_path_uses_runtime_root_relative_path(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "shared"
    project_dir = tmp_path / "project"
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(runtime_dir))

    assert display_path(
        runtime_dir / "config" / "drafts" / "日报.json",
        project_dir=project_dir,
    ) == "config/drafts/日报.json"


def test_runtime_drawer_does_not_prepend_legacy_runtime_prefix():
    project_root = Path(__file__).resolve().parents[1]
    source = (
        project_root / "frontend" / "src" / "components" / "runtime" / "RuntimeDrawer.tsx"
    ).read_text(encoding="utf-8")

    assert "runtime/{props.currentPath}" not in source
    assert 'props.currentPath || "."' in source


def test_runtime_compatibility_audit_records_retirement():
    project_root = Path(__file__).resolve().parents[1]
    audit = (project_root / "docs" / "runtime-path-compatibility-audit.md").read_text(
        encoding="utf-8"
    )

    assert "已下线" in audit
    assert "待迁移" not in audit
    assert "resolve_runtime_relative_path()" in audit
    assert "rg -n 'resolve_runtime_path\\(|validate_runtime_path\\('" in audit


