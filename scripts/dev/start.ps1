param(
    [switch]$ForceRestart,
    [switch]$SkipAdmin,
    [switch]$ClearScheduledBacklog
)

$ErrorActionPreference = "Stop"

if ($ClearScheduledBacklog) {
    Write-Warning "ClearScheduledBacklog 已废弃且不会自动取消任何运行。请在 Prefect UI 中显式处理。"
}

& (Join-Path (Split-Path -Parent $PSScriptRoot) "run.ps1") `
    -ForceRestart:$ForceRestart `
    -SkipWeb:$SkipAdmin
