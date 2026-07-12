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
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout.splitlines()[-1])


def test_migration_copies_legacy_cookie_and_profile_only_when_targets_absent(tmp_path):
    repo_root = tmp_path / "repo"
    runtime_root = tmp_path / "shared"
    legacy_cookie = repo_root / "runtime" / "cookies" / "cookie_dump.json"
    legacy_profile = repo_root / "runtime" / "browser_session" / "edge_profile_auto_login"
    legacy_cookie.parent.mkdir(parents=True)
    legacy_profile.mkdir(parents=True)
    legacy_cookie.write_text('{"secret":"legacy"}', encoding="utf-8")
    (legacy_profile / "marker.txt").write_text("legacy-profile", encoding="utf-8")

    first = run_migration(repo_root, runtime_root)

    target_cookie = runtime_root / "cookies" / "cookie_dump.json"
    target_profile = runtime_root / "browser_session" / "edge_profile_auto_login"
    assert target_cookie.read_text(encoding="utf-8") == '{"secret":"legacy"}'
    assert (target_profile / "marker.txt").read_text(encoding="utf-8") == "legacy-profile"
    assert {item["status"] for item in first} == {"copied"}

    target_cookie.write_text('{"secret":"newer"}', encoding="utf-8")
    (target_profile / "marker.txt").write_text("newer-profile", encoding="utf-8")
    second = run_migration(repo_root, runtime_root)

    assert target_cookie.read_text(encoding="utf-8") == '{"secret":"newer"}'
    assert (target_profile / "marker.txt").read_text(encoding="utf-8") == "newer-profile"
    assert {item["status"] for item in second} == {"target_exists"}


def test_migration_does_not_publish_partial_profile_when_copy_fails(tmp_path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    assert pwsh is not None
    repo_root = tmp_path / "repo"
    runtime_root = tmp_path / "shared"
    legacy_profile = repo_root / "runtime" / "browser_session" / "edge_profile_auto_login"
    legacy_profile.mkdir(parents=True)
    (legacy_profile / "marker.txt").write_text("legacy-profile", encoding="utf-8")
    script = (ROOT / "scripts" / "lib" / "runtime_state_migration.ps1").as_posix()
    command = f"""
$ErrorActionPreference = 'Stop'
. '{script}'
function Copy-Item {{
  param([string]$LiteralPath, [string]$Destination, [switch]$Recurse)
  [IO.Directory]::CreateDirectory($Destination) | Out-Null
  [IO.File]::WriteAllText((Join-Path $Destination 'partial.txt'), 'partial')
  throw 'injected copy failure'
}}
try {{
  Invoke-RuntimeStateMigration -RepoRoot '{repo_root.as_posix()}' -RuntimeRoot '{runtime_root.as_posix()}' | Out-Null
}} catch {{
  exit 23
}}
exit 0
"""

    completed = subprocess.run([pwsh, "-NoProfile", "-Command", command])

    assert completed.returncode == 23
    assert not (runtime_root / "browser_session" / "edge_profile_auto_login").exists()
