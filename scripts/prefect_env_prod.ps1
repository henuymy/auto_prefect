$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PrefectHome = Join-Path $RepoRoot "runtime\prefect_home"
$UnifiedLocalEnvPath = Join-Path $PSScriptRoot "environment.local.ps1"
$LegacyLocalEnvPath = Join-Path $PSScriptRoot "prefect_env_prod.local.ps1"
. (Join-Path $PSScriptRoot "lib\runtime_config.ps1")
if (Import-ProjectRuntimeConfig) {
    # runtime.local.json is the preferred local configuration source.
} elseif (Test-Path -LiteralPath $UnifiedLocalEnvPath) {
    . $UnifiedLocalEnvPath
} elseif (Test-Path -LiteralPath $LegacyLocalEnvPath) {
    . $LegacyLocalEnvPath
}
. (Join-Path $PSScriptRoot "tools\dashboard\mysql_env.ps1")
$SourceDatabaseUrl = $env:AUTO_NOTIFY_PREFECT_DATABASE_URL
if (-not $SourceDatabaseUrl) {
    throw "缺少 Prefect 数据库连接串，请在 scripts\prefect_env_prod.local.ps1 中设置 `$env:AUTO_NOTIFY_PREFECT_DATABASE_URL"
}
if ($SourceDatabaseUrl -notmatch "/prefect(?:\?.*)?$") {
    throw "基础连接串必须以 /prefect 结尾，无法安全派生 prefect_dev"
}
$DatabaseUrl = $SourceDatabaseUrl -replace "/prefect(\?.*)?$", "/prefect_dev`$1"
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
Write-Host "DatabaseUrl    : PostgreSQL / prefect_dev"
Write-Host "说明           : 当前项目仅允许使用 prefect_dev，拒绝连接 prefect。"
