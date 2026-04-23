$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PrefectHome = Join-Path $RepoRoot "runtime\prefect_home"
$DatabaseUrl = "sqlite+aiosqlite:///C:/Users/yuyu/Desktop/项目/自动通报/runtime/prefect_home/prefect.db"

New-Item -ItemType Directory -Force -Path $PrefectHome | Out-Null

$env:PREFECT_HOME = $PrefectHome
$env:PREFECT_API_URL = "http://127.0.0.1:4200/api"
$env:PREFECT_API_DATABASE_CONNECTION_URL = $DatabaseUrl
$env:PREFECT_SERVER_DATABASE_CONNECTION_URL = $DatabaseUrl
$env:PREFECT_API_DATABASE_TIMEOUT = "60"
$env:PREFECT_SERVER_DATABASE_TIMEOUT = "60"
$env:PREFECT_API_SERVICES_SCHEDULER_ENABLED = "False"
$env:PREFECT_API_SERVICES_LATE_RUNS_ENABLED = "False"
$env:PREFECT_SERVER_ANALYTICS_ENABLED = "False"

Write-Host "已加载 Prefect 本地调试环境变量。"
Write-Host "PREFECT_HOME   : $($env:PREFECT_HOME)"
Write-Host "PREFECT_API_URL: $($env:PREFECT_API_URL)"
Write-Host "DatabaseUrl    : $DatabaseUrl"
Write-Host "说明           : SQLite 调试模式，仅适合本地手动测试。"
