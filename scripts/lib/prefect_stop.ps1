param(
    [int[]]$Ports = @(4200),
    [string]$PrefectHome = "",
    [switch]$KillAutoNotifyPython = $true
)

$ErrorActionPreference = "Continue"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot "python_env.ps1")
if (-not $PrefectHome) {
    $PrefectHome = Join-Path $RepoRoot "runtime\prefect_home"
}
$ProjectPython = Get-ProjectPython

Write-Host "RepoRoot        : $RepoRoot"
Write-Host "PREFECT_HOME    : $PrefectHome"
Write-Host "Python          : $ProjectPython"
Write-Host ""

$killedByPort = @()
foreach ($port in $Ports) {
    $listeners = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        if ($listener.OwningProcess -and -not ($killedByPort -contains $listener.OwningProcess)) {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
            $killedByPort += $listener.OwningProcess
        }
    }
}

$killedPython = @()
if ($KillAutoNotifyPython) {
    $py = Get-Process -Name "python" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and ($_.Path -ieq $ProjectPython) }
    foreach ($proc in $py) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        $killedPython += $proc.Id
    }
}

$killedShell = @()
$prefectShells = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -in @("pwsh.exe", "powershell.exe") -and
        $_.CommandLine -and
        (
            $_.CommandLine -like "*prefect server start*" -or
            $_.CommandLine -like "*prefect worker start*" -or
            $_.CommandLine -like "*Prefect Server*" -or
            $_.CommandLine -like "*Prefect Worker*"
        )
    }
foreach ($proc in $prefectShells) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    $killedShell += $proc.ProcessId
}

# 兼容清理：如果曾经用 SQLite 调试，停止时顺手清掉 WAL/SHM 锁文件。
foreach ($homePath in @($PrefectHome, (Join-Path $env:USERPROFILE ".prefect"))) {
    if (-not (Test-Path -LiteralPath $homePath)) {
        continue
    }
    Remove-Item -LiteralPath (Join-Path $homePath "prefect.db-wal") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $homePath "prefect.db-shm") -Force -ErrorAction SilentlyContinue
}

Remove-Item -LiteralPath (Join-Path $RepoRoot "runtime\locks\excel_com.lock") -Force -ErrorAction SilentlyContinue

$CleanupPython = $ProjectPython
Push-Location $RepoRoot
try {
    & $CleanupPython -c "from services.browser_session import close_recorded_browser_session; print(close_recorded_browser_session())" 2>$null
} finally {
    Pop-Location
}

Write-Host "Stopped by port: $($killedByPort -join ',')"
Write-Host "Stopped python : $($killedPython -join ',')"
Write-Host "Stopped shell  : $($killedShell -join ',')"
Write-Host "Done."
