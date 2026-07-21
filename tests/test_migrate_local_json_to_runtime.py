from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_local_json_to_runtime.py"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_project_with_legacy_files(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    write_json(project_root / "config" / "dashboard" / "session.json", {"schema_version": 2})
    write_json(project_root / "config" / "modules" / "login_config.json", {"credentials": {}})
    write_json(project_root / "config" / "tasks" / "日报通报.json", {"flow_name": "auto-notify-flow"})
    write_json(
        project_root / "config" / "runtime.local.json",
        {
            "dashboard": {"session_overrides": {}},
            "module_overrides": {},
            "task_overrides": {},
        },
    )
    write_json(
        project_root / "config" / "dashboard" / "session.local.json",
        {"collection_database_lock_name": "legacy-secret-lock"},
    )
    write_json(
        project_root / "config" / "modules" / "login_config.local.json",
        {"credentials": {"username": "legacy-user", "password": "legacy-secret"}},
    )
    write_json(
        project_root / "config" / "tasks" / "日报通报.local.json",
        {"retry": {"attempts": 2}},
    )
    return project_root


def run_migration(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(project_root), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_preview_lists_mappings_without_writing_or_deleting(tmp_path: Path) -> None:
    project_root = make_project_with_legacy_files(tmp_path)

    result = run_migration(project_root)

    assert result.returncode == 0
    assert "PREVIEW config/dashboard/session.local.json -> dashboard.session_overrides" in result.stdout
    assert "PREVIEW config/modules/login_config.local.json -> module_overrides.login_config" in result.stdout
    assert "PREVIEW config/tasks/日报通报.local.json -> task_overrides.日报通报" in result.stdout
    assert "legacy-secret" not in result.stdout
    assert (project_root / "config" / "modules" / "login_config.local.json").exists()
    assert read_json(project_root / "config" / "runtime.local.json")["module_overrides"] == {}


def test_apply_merges_legacy_files_without_deleting_them(tmp_path: Path) -> None:
    project_root = make_project_with_legacy_files(tmp_path)

    result = run_migration(project_root, "--apply")

    assert result.returncode == 0
    assert "APPLIED config/dashboard/session.local.json -> dashboard.session_overrides" in result.stdout
    assert "APPLIED config/modules/login_config.local.json -> module_overrides.login_config" in result.stdout
    runtime = read_json(project_root / "config" / "runtime.local.json")
    assert runtime["dashboard"]["session_overrides"] == {
        "collection_database_lock_name": "legacy-secret-lock"
    }
    assert runtime["module_overrides"]["login_config"] == {
        "credentials": {"username": "legacy-user", "password": "legacy-secret"}
    }
    assert runtime["task_overrides"]["日报通报"] == {"retry": {"attempts": 2}}
    assert (project_root / "config" / "modules" / "login_config.local.json").exists()


def test_conflicting_value_refuses_apply_and_keeps_legacy_files(tmp_path: Path) -> None:
    project_root = make_project_with_legacy_files(tmp_path)
    runtime_path = project_root / "config" / "runtime.local.json"
    runtime = read_json(runtime_path)
    runtime["module_overrides"] = {
        "login_config": {"credentials": {"username": "central-user"}}
    }
    write_json(runtime_path, runtime)

    result = run_migration(project_root, "--apply")

    assert result.returncode == 2
    assert "CONFLICT module_overrides.login_config.credentials.username" in result.stdout
    assert "legacy-user" not in result.stdout
    assert "legacy-secret" not in result.stdout
    assert read_json(runtime_path)["module_overrides"] == {
        "login_config": {"credentials": {"username": "central-user"}}
    }
    assert (project_root / "config" / "modules" / "login_config.local.json").exists()


def test_apply_remove_legacy_deletes_only_after_successful_migration(tmp_path: Path) -> None:
    project_root = make_project_with_legacy_files(tmp_path)

    result = run_migration(project_root, "--apply", "--remove-legacy")

    assert result.returncode == 0
    assert "REMOVED config/dashboard/session.local.json" in result.stdout
    assert "REMOVED config/modules/login_config.local.json" in result.stdout
    assert "REMOVED config/tasks/日报通报.local.json" in result.stdout
    assert not (project_root / "config" / "dashboard" / "session.local.json").exists()
    assert not (project_root / "config" / "modules" / "login_config.local.json").exists()
    assert not (project_root / "config" / "tasks" / "日报通报.local.json").exists()


def test_remove_legacy_requires_apply(tmp_path: Path) -> None:
    project_root = make_project_with_legacy_files(tmp_path)

    result = run_migration(project_root, "--remove-legacy")

    assert result.returncode == 2
    assert "--remove-legacy 只能与 --apply 一起使用" in result.stderr
    assert (project_root / "config" / "modules" / "login_config.local.json").exists()


def test_invalid_legacy_json_refuses_apply_without_changing_runtime(tmp_path: Path) -> None:
    project_root = make_project_with_legacy_files(tmp_path)
    runtime_path = project_root / "config" / "runtime.local.json"
    before = runtime_path.read_text(encoding="utf-8")
    (project_root / "config" / "modules" / "login_config.local.json").write_text(
        "{",
        encoding="utf-8",
    )

    result = run_migration(project_root, "--apply")

    assert result.returncode == 2
    assert "ERROR JSON 无法解析: config/modules/login_config.local.json" in result.stdout
    assert runtime_path.read_text(encoding="utf-8") == before
    assert (project_root / "config" / "modules" / "login_config.local.json").exists()
