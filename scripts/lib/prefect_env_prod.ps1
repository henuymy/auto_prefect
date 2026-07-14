$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$PrefectHome = Join-Path $RepoRoot "runtime\prefect_home"
$ScriptsRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "runtime_config.ps1")
if (-not (Import-ProjectRuntimeConfig)) {
    throw "缺少运行配置。请创建 config\runtime.local.json。"
}
. (Join-Path $ScriptsRoot "tools\dashboard\mysql_env.ps1")
$SourceDatabaseUrl = $env:AUTO_NOTIFY_PREFECT_DATABASE_URL
if (-not $SourceDatabaseUrl) {
    throw "缺少 Prefect 数据库连接串，请在 config\runtime.local.json 中设置 prefect.postgres.url。"
}
$DatabaseUrl = $SourceDatabaseUrl
$env:AUTO_NOTIFY_PREFECT_DEV_DATABASE_URL = $DatabaseUrl

New-Item -ItemType Directory -Force -Path $PrefectHome | Out-Null

$env:PREFECT_HOME = $PrefectHome
if (-not $env:PREFECT_API_URL) {
    $env:PREFECT_API_URL = "http://127.0.0.1:4200/api"
}
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

Write-Host "已加载 Prefect 开发调度环境变量。"
Write-Host "PREFECT_HOME   : $($env:PREFECT_HOME)"
Write-Host "PREFECT_API_URL: $($env:PREFECT_API_URL)"
Write-Host "DatabaseUrl    : PostgreSQL / configured database"
Write-Host "说明           : 当前项目直接使用配置中的 PostgreSQL 数据库。"
