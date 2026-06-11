$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PrefectHome = Join-Path $RepoRoot "runtime\prefect_home"
$LocalEnvPath = Join-Path $PSScriptRoot "prefect_env_prod.local.ps1"
if (Test-Path -LiteralPath $LocalEnvPath) {
    . $LocalEnvPath
}
. (Join-Path $PSScriptRoot "dashboard\mysql_env.ps1")
$DatabaseUrl = $env:AUTO_NOTIFY_PREFECT_DATABASE_URL
if (-not $DatabaseUrl) {
    throw "缺少 Prefect 数据库连接串，请在 scripts\prefect_env_prod.local.ps1 中设置 `$env:AUTO_NOTIFY_PREFECT_DATABASE_URL"
}

New-Item -ItemType Directory -Force -Path $PrefectHome | Out-Null

$env:PREFECT_HOME = $PrefectHome
$env:PREFECT_API_URL = "http://127.0.0.1:4200/api"
$env:PREFECT_API_DATABASE_CONNECTION_URL = $DatabaseUrl
$env:PREFECT_SERVER_DATABASE_CONNECTION_URL = $DatabaseUrl
$env:PREFECT_API_DATABASE_TIMEOUT = "60"
$env:PREFECT_SERVER_DATABASE_TIMEOUT = "60"
$env:PREFECT_API_SERVICES_SCHEDULER_ENABLED = "True"
$env:PREFECT_API_SERVICES_LATE_RUNS_ENABLED = "False"
$env:PREFECT_SERVER_ANALYTICS_ENABLED = "False"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONNOUSERSITE = "1"

Write-Host "已加载 Prefect 正式调度环境变量。"
Write-Host "PREFECT_HOME   : $($env:PREFECT_HOME)"
Write-Host "PREFECT_API_URL: $($env:PREFECT_API_URL)"
Write-Host "DatabaseUrl    : 已从本机 local 配置加载"
Write-Host "说明           : PostgreSQL 调度模式，适合长期运行。"
