param(
    [ValidateSet("server", "worker", "both")]
    [string]$Mode = "both",
    [string]$ApiUrl = "http://127.0.0.1:4200/api",
    [string]$WorkPool = "default-agent-pool",
    [string]$PrefectHome = "",
    [string]$PythonExe = "",
    [switch]$Detached
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $PrefectHome) {
    $PrefectHome = Join-Path $RepoRoot "runtime\prefect_home"
}
if (-not $PythonExe) {
    $PythonExe = Join-Path $env:USERPROFILE ".conda\envs\auto-notify\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = "python"
}

New-Item -ItemType Directory -Force -Path $PrefectHome | Out-Null

$env:PREFECT_HOME = $PrefectHome
$env:PREFECT_API_URL = $ApiUrl

Write-Host "RepoRoot      : $RepoRoot"
Write-Host "PythonExe     : $PythonExe"
Write-Host "PREFECT_HOME  : $($env:PREFECT_HOME)"
Write-Host "PREFECT_API_URL: $($env:PREFECT_API_URL)"
Write-Host "Mode          : $Mode"
Write-Host ""

function Start-DetachedWindow {
    param(
        [string]$Title,
        [string]$Command
    )
    $escaped = $Command.Replace('"', '\"')
    Start-Process -FilePath "pwsh" -ArgumentList @(
        "-NoExit",
        "-Command",
        "`$Host.UI.RawUI.WindowTitle='$Title'; $escaped"
    ) | Out-Null
}

switch ($Mode) {
    "server" {
        if ($Detached) {
            Start-DetachedWindow -Title "Prefect Server" -Command @"
`$env:PREFECT_HOME = '$($env:PREFECT_HOME)'
& '$PythonExe' -m prefect server start
"@
            Write-Host "已在新窗口启动 Prefect Server。"
        } else {
            & $PythonExe -m prefect server start
        }
    }
    "worker" {
        if ($Detached) {
            Start-DetachedWindow -Title "Prefect Worker" -Command @"
`$env:PREFECT_HOME = '$($env:PREFECT_HOME)'
`$env:PREFECT_API_URL = '$($env:PREFECT_API_URL)'
& '$PythonExe' -m prefect worker start --pool '$WorkPool' --type process
"@
            Write-Host "已在新窗口启动 Prefect Worker。"
        } else {
            & $PythonExe -m prefect worker start --pool $WorkPool --type process
        }
    }
    "both" {
        Start-DetachedWindow -Title "Prefect Server" -Command @"
`$env:PREFECT_HOME = '$($env:PREFECT_HOME)'
& '$PythonExe' -m prefect server start
"@
        Start-Sleep -Seconds 2
        Start-DetachedWindow -Title "Prefect Worker" -Command @"
`$env:PREFECT_HOME = '$($env:PREFECT_HOME)'
`$env:PREFECT_API_URL = '$($env:PREFECT_API_URL)'
& '$PythonExe' -m prefect worker start --pool '$WorkPool' --type process
"@
        Write-Host "已在两个新窗口启动 Server + Worker。"
        Write-Host "打开 UI: http://127.0.0.1:4200"
    }
}

