param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [int]$DashboardPort = 5174,
    [int]$MonitorPort = 5175
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\runtime_config.ps1")
Import-ProjectRuntimeConfig | Out-Null
. (Join-Path $PSScriptRoot "lib\process_registry.ps1")

@(
    "prefect-worker-notify",
    "prefect-worker-dashboard",
    "prefect-worker-session",
    "web-dashboard",
    "web-monitor",
    "web-frontend",
    "web-backend",
    "prefect-server"
) | ForEach-Object { Stop-ManagedProcessTree -Name $_ | Out-Null }

Write-Host "运行栈已停止。"
