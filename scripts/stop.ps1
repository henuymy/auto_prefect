param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$KillAutoNotifyPython = $true
)

$ErrorActionPreference = "Continue"

. (Join-Path $PSScriptRoot "lib\runtime_config.ps1")
Import-ProjectRuntimeConfig | Out-Null

& (Join-Path $PSScriptRoot "prefect_stop.ps1") `
    -Ports @(4200) `
    -KillAutoNotifyPython:$KillAutoNotifyPython

foreach ($port in @($BackendPort, $FrontendPort) | Select-Object -Unique) {
    Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        ForEach-Object {
            Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
        }
}

Write-Host "运行栈已停止。"
