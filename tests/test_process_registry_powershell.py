import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
REGISTRY = (ROOT / "scripts" / "lib" / "process_registry.ps1").as_posix()
STATUS = (ROOT / "scripts" / "status.ps1").as_posix()


def test_startup_claim_rejects_second_process_before_release(tmp_path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    assert pwsh is not None
    registry = (ROOT / "scripts" / "lib" / "process_registry.ps1").as_posix()
    runtime_root = tmp_path.as_posix()
    command = f"""
$ErrorActionPreference = 'Stop'
$env:AUTO_NOTIFY_RUNTIME_ROOT = '{runtime_root}'
. '{registry}'
$claim = Enter-StartupClaim
try {{
  $child = @'
$ErrorActionPreference = "Stop"
$env:AUTO_NOTIFY_RUNTIME_ROOT = "{runtime_root}"
. "{registry}"
try {{ Enter-StartupClaim | Out-Null; exit 0 }} catch {{ exit 23 }}
'@ | & '{pwsh}' -NoProfile -Command -
  if ($LASTEXITCODE -ne 23) {{ throw "second startup claim was not rejected" }}
}} finally {{
  Exit-StartupClaim -Claim $claim
}}
"""

    subprocess.run([pwsh, "-NoProfile", "-Command", command], check=True)


def _run_powershell(command: str) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL is not None
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _json_result(completed: subprocess.CompletedProcess[str]) -> dict:
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout.splitlines()[-1])


def test_status_lock_metadata_accepts_login_and_excel_timestamp_fields(tmp_path):
    runtime_root = tmp_path.as_posix()
    command = f"""
$ErrorActionPreference = 'Stop'
$env:AUTO_NOTIFY_RUNTIME_ROOT = '{runtime_root}'
$lockRoot = Join-Path $env:AUTO_NOTIFY_RUNTIME_ROOT 'session\locks'
New-Item -ItemType Directory -Force -Path $lockRoot | Out-Null
@{{ pid = 111; created_at = ([DateTimeOffset]::Now.AddSeconds(-30).ToString('o')); secret = 'login-secret' }} |
  ConvertTo-Json | Set-Content (Join-Path $lockRoot 'login.lock') -Encoding UTF8
@{{ pid = 222; acquired_at = ([DateTimeOffset]::Now.AddSeconds(-45).ToString('o')); token = 'excel-secret' }} |
  ConvertTo-Json | Set-Content (Join-Path $lockRoot 'excel_com.lock') -Encoding UTF8

$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
  '{STATUS}', [ref]$tokens, [ref]$errors
)
$function = $ast.Find({{
  param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Write-LockStatus'
}}, $true)
Invoke-Expression $function.Extent.Text
$lines = @(
  Write-LockStatus -FileName 'login.lock' 6>&1
  Write-LockStatus -FileName 'excel_com.lock' 6>&1
)
[pscustomobject]@{{ lines = @($lines | ForEach-Object {{ [string]$_ }}) }} |
  ConvertTo-Json -Compress
"""

    payload = _json_result(_run_powershell(command))
    output = "\n".join(payload["lines"])
    assert "login.lock: owner PID=111" in output
    assert "excel_com.lock: owner PID=222" in output
    assert "held seconds=" in output
    assert "login-secret" not in output
    assert "excel-secret" not in output


def test_process_tree_snapshot_captures_root_and_child_start_identities(tmp_path):
    runtime_root = tmp_path.as_posix()
    command = f"""
$ErrorActionPreference = 'Stop'
$env:AUTO_NOTIFY_RUNTIME_ROOT = '{runtime_root}'
. '{REGISTRY}'
$startProcessArgs = @{{
  FilePath = 'pwsh'
  ArgumentList = @(
  '-NoProfile', '-Command', 'Start-Sleep -Seconds 30'
  )
  PassThru = $true
}}
if ($IsWindows) {{
  $startProcessArgs.WindowStyle = 'Hidden'
}}
$child = Start-Process @startProcessArgs
try {{
  Start-Sleep -Milliseconds 300
  $root = Get-Process -Id $PID
  Register-ManagedProcess -Name 'snapshot-test' -Process $root -Command 'test' | Out-Null
  $record = Get-ManagedProcessRecord -Name 'snapshot-test'
  $snapshot = @(Get-ManagedProcessTreeSnapshot -RootRecord $record)
  $rootIdentity = $snapshot | Where-Object {{ [int]$_.pid -eq $PID }} | Select-Object -First 1
  $childIdentity = $snapshot | Where-Object {{ [int]$_.pid -eq $child.Id }} | Select-Object -First 1
  [pscustomobject]@{{
    root_captured = $null -ne $rootIdentity
    child_captured = $null -ne $childIdentity
    root_valid = Test-ManagedProcessIdentity -Identity $rootIdentity
    child_valid = Test-ManagedProcessIdentity -Identity $childIdentity
  }} | ConvertTo-Json -Compress
}} finally {{
  if ($null -ne (Get-Process -Id $child.Id -ErrorAction SilentlyContinue)) {{
    Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue
  }}
}}
"""

    payload = _json_result(_run_powershell(command))
    assert payload == {
        "root_captured": True,
        "child_captured": True,
        "root_valid": True,
        "child_valid": True,
    }


def _stop_scenario_command(tmp_path, overrides: str) -> str:
    runtime_root = tmp_path.as_posix()
    return f"""
$ErrorActionPreference = 'Stop'
$env:AUTO_NOTIFY_RUNTIME_ROOT = '{runtime_root}'
. '{REGISTRY}'
$process = Get-Process -Id $PID
Register-ManagedProcess -Name 'stop-test' -Process $process -Command 'test' | Out-Null
function taskkill.exe {{ $global:LASTEXITCODE = 1 }}
{overrides}
$threw = $false
try {{
  $result = Stop-ManagedProcessTree -Name 'stop-test'
}} catch {{
  $threw = $true
  $message = $_.Exception.Message
}}
$recordExists = Test-Path (Join-Path $env:AUTO_NOTIFY_RUNTIME_ROOT 'processes\\stop-test.json')
[pscustomobject]@{{
  threw = $threw
  message = $message
  result = $result
  record_exists = $recordExists
}} | ConvertTo-Json -Compress
"""


def test_stop_retains_registry_and_raises_when_taskkill_fails(tmp_path):
    overrides = """
function Get-ManagedProcessTreeSnapshot { param($RootRecord) @($RootRecord) }
function Invoke-ManagedTaskkill { param($ProcessId) return $false }
"""

    payload = _json_result(
        _run_powershell(_stop_scenario_command(tmp_path, overrides))
    )

    assert payload["threw"] is True
    assert "taskkill" in payload["message"].lower()
    assert payload["record_exists"] is True


def test_stop_retains_registry_and_raises_when_descendant_identity_remains(tmp_path):
    overrides = """
function Get-ManagedProcessTreeSnapshot {
  param($RootRecord)
  @(
    $RootRecord,
    [pscustomobject]@{ pid = 999999; process_started_at = '2000-01-01T00:00:00+00:00' }
  )
}
function Invoke-ManagedTaskkill { param($ProcessId) return $true }
function Wait-ManagedProcessTreeExit { param($Snapshot) return @($Snapshot[1]) }
"""

    payload = _json_result(
        _run_powershell(_stop_scenario_command(tmp_path, overrides))
    )

    assert payload["threw"] is True
    assert "999999" in payload["message"]
    assert payload["record_exists"] is True


def test_stop_deletes_registry_only_after_every_captured_identity_exits(tmp_path):
    overrides = """
function Get-ManagedProcessTreeSnapshot {
  param($RootRecord)
  @(
    $RootRecord,
    [pscustomobject]@{ pid = 888888; process_started_at = '2000-01-01T00:00:00+00:00' }
  )
}
function Invoke-ManagedTaskkill { param($ProcessId) return $true }
function Wait-ManagedProcessTreeExit { param($Snapshot) return @() }
"""

    payload = _json_result(
        _run_powershell(_stop_scenario_command(tmp_path, overrides))
    )

    assert payload["threw"] is False
    assert payload["result"] is True
    assert payload["record_exists"] is False
