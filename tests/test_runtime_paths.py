import os
import subprocess
import sys
from pathlib import Path

from services.runtime_paths import resolve_runtime_path, runtime_path, runtime_root


def test_runtime_relative_paths_rebase_to_external_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))

    assert resolve_runtime_path(
        "runtime/session/cookie_dump.json", project_dir=tmp_path
    ) == (tmp_path / "shared" / "session" / "cookie_dump.json").resolve()


def test_non_runtime_relative_paths_remain_project_relative(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))

    assert resolve_runtime_path("config/tasks/demo.json", project_dir=tmp_path) == (
        tmp_path / "config" / "tasks" / "demo.json"
    ).resolve()


def test_absolute_paths_are_preserved_for_internal_callers(monkeypatch, tmp_path):
    target = (tmp_path / "absolute.json").resolve()
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(tmp_path / "shared"))

    assert resolve_runtime_path(target, project_dir=tmp_path) == target


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
