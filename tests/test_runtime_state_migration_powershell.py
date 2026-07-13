import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def run_migration(repo_root: Path, runtime_root: Path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    assert pwsh is not None
    script = (ROOT / "scripts" / "lib" / "runtime_state_migration.ps1").as_posix()
    command = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. '{script}'
Invoke-RuntimeStateMigration -RepoRoot '{repo_root.as_posix()}' -RuntimeRoot '{runtime_root.as_posix()}' | ConvertTo-Json -Compress
"""
    completed = subprocess.run([pwsh, "-NoProfile", "-Command", command], check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(completed.stdout.splitlines()[-1])


def test_one_time_migration_moves_legacy_session_state_and_removes_legacy_paths(tmp_path):
    repo_root = tmp_path / "repo"
    runtime_root = tmp_path / "shared"
    cookie = runtime_root / "cookies" / "cookie_dump.json"
    profile = runtime_root / "browser_session" / "edge_profile_auto_login"
    cookie.parent.mkdir(parents=True)
    profile.mkdir(parents=True)
    cookie.write_text('{"state":"legacy"}', encoding="utf-8")
    (profile / "marker.txt").write_text("profile", encoding="utf-8")

    results = run_migration(repo_root, runtime_root)

    assert {item["name"]: item["status"] for item in results}["cookie_dump"] == "moved"
    assert (runtime_root / "session" / "cookie_dump.json").exists()
    assert (runtime_root / "session" / "browser-profile" / "marker.txt").exists()
    assert not (runtime_root / "cookies").exists()
    assert not (runtime_root / "browser_session").exists()


def test_migration_never_recreates_a_removed_legacy_source(tmp_path):
    repo_root = tmp_path / "repo"
    runtime_root = tmp_path / "shared"

    results = run_migration(repo_root, runtime_root)

    assert {item["status"] for item in results} == {"source_absent"}
    assert not (runtime_root / "cookies").exists()


def test_migration_moves_legacy_config_drafts_without_deleting_unrelated_files(tmp_path):
    repo_root = tmp_path / "repo"
    runtime_root = tmp_path / "shared"
    draft = repo_root / "runtime" / "drafts" / "日报.json"
    unrelated = repo_root / "runtime" / "unmanaged" / "keep.txt"
    draft.parent.mkdir(parents=True)
    draft.write_text('{"name":"日报"}', encoding="utf-8")
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("keep", encoding="utf-8")

    run_migration(repo_root, runtime_root)

    assert (runtime_root / "config" / "drafts" / "日报.json").exists()
    assert not draft.exists()
    assert unrelated.exists()
