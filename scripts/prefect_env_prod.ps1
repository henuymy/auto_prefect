$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PrefectHome = Join-Path $RepoRoot "runtime\prefect_home"
$DatabaseUrl = "postgresql+asyncpg://user_rEkhna:password_YSDPae@60.205.108.31:5432/prefect"

New-Item -ItemType Directory -Force -Path $PrefectHome | Out-Null

$env:PREFECT_HOME = $PrefectHome
$env:PREFECT_API_URL = "http://127.0.0.1:4200/api"
$env:PREFECT_API_DATABASE_CONNECTION_URL = $DatabaseUrl
$env:PREFECT_SERVER_DATABASE_CONNECTION_URL = $DatabaseUrl
$env:PREFECT_API_DATABASE_TIMEOUT = "60"
$env:PREFECT_SERVER_DATABASE_TIMEOUT = "60"
$env:PREFECT_API_SERVICES_SCHEDULER_ENABLED = "True"
$env:PREFECT_API_SERVICES_LATE_RUNS_ENABLED = "True"
$env:PREFECT_SERVER_ANALYTICS_ENABLED = "False"

Write-Host "已加载 Prefect 正式调度环境变量。"
Write-Host "PREFECT_HOME   : $($env:PREFECT_HOME)"
Write-Host "PREFECT_API_URL: $($env:PREFECT_API_URL)"
Write-Host "DatabaseUrl    : $DatabaseUrl"
Write-Host "说明           : PostgreSQL 调度模式，适合长期运行。"
