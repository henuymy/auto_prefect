param(
    [int[]]$Ports = @(4200),
    [string]$PrefectHome = "",
    [switch]$KillAutoNotifyPython = $true
)

$ErrorActionPreference = "Continue"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $PrefectHome) {
    $PrefectHome = Join-Path $RepoRoot "runtime\prefect_home"
}
$AutoNotifyPython = Join-Path $env:USERPROFILE ".conda\envs\auto-notify\python.exe"

Write-Host "RepoRoot       : $RepoRoot"
Write-Host "PREFECT_HOME   : $PrefectHome"
Write-Host "AutoNotifyPython: $AutoNotifyPython"
Write-Host ""

# 1) 按端口停止 Prefect Server
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

# 2) 可选：停止 auto-notify 环境下的 python（通常是 worker）
$killedPython = @()
if ($KillAutoNotifyPython) {
    $py = Get-Process -Name "python" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and ($_.Path -ieq $AutoNotifyPython) }
    foreach ($proc in $py) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        $killedPython += $proc.Id
    }
}

# 3) 清理 SQLite 锁文件（避免 locked/readonly 残留）
$homesToClean = @(
    $PrefectHome,
    (Join-Path $env:USERPROFILE ".prefect")
)
foreach ($home in $homesToClean) {
    if (-not (Test-Path -LiteralPath $home)) {
        continue
    }
    attrib -R "$home\*" /S /D | Out-Null
    Remove-Item -LiteralPath (Join-Path $home "prefect.db-wal") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $home "prefect.db-shm") -Force -ErrorAction SilentlyContinue
}

Write-Host "Stopped by port: $($killedByPort -join ',')"
Write-Host "Stopped python : $($killedPython -join ',')"
Write-Host "Done."

