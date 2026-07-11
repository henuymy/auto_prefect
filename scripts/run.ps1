param(
    [string]$ApiUrl = "",
    [string]$WorkPool = "",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$ForceRestart,
    [switch]$UseSqliteDebug,
    [switch]$SkipWeb
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "lib\python_env.ps1")
. (Join-Path $PSScriptRoot "lib\runtime_config.ps1")
$UnifiedLocalEnvPath = Join-Path $PSScriptRoot "environment.local.ps1"
$LegacyLocalEnvPath = Join-Path $PSScriptRoot "prefect_env_prod.local.ps1"
if (-not (Import-ProjectRuntimeConfig)) {
    if (Test-Path -LiteralPath $UnifiedLocalEnvPath) {
        . $UnifiedLocalEnvPath
    } elseif (Test-Path -LiteralPath $LegacyLocalEnvPath) {
        . $LegacyLocalEnvPath
    } else {
        throw "缺少运行配置。请创建 config\runtime.local.json，或在迁移期使用 scripts\environment.local.ps1。"
    }
}
. (Join-Path $PSScriptRoot "tools\dashboard\mysql_env.ps1")

if (-not $ApiUrl) {
    $ApiUrl = $env:PREFECT_API_URL
}
if (-not $WorkPool) {
    $WorkPool = $env:PREFECT_WORK_POOL_NAME
}
$PythonExe = Get-ProjectPython

function Test-RuntimeDatabaseConnections {
    $checkScript = @'
import asyncio
import os
import sys

import asyncpg
import pymysql

async def check_postgres():
    url = os.environ["AUTO_NOTIFY_PREFECT_DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    connection = await asyncpg.connect(url, timeout=15)
    try:
        await connection.fetchval("select 1")
    finally:
        await connection.close()

def check_mysql():
    connection = pymysql.connect(
        host=os.environ["DASHBOARD_MYSQL_HOST"],
        port=int(os.environ["DASHBOARD_MYSQL_PORT"]),
        user=os.environ["DASHBOARD_MYSQL_USER"],
        password=os.environ["DASHBOARD_MYSQL_PASSWORD"],
        database=os.environ["DASHBOARD_MYSQL_DATABASE"],
        connect_timeout=15,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("select 1")
    finally:
        connection.close()

asyncio.run(check_postgres())
check_mysql()
print("runtime_databases_ok")
'@

    Write-Host "校验 PostgreSQL 与 MySQL 连接..."
    $checkScript | & $PythonExe -
    if ($LASTEXITCODE -ne 0) {
        throw "运行数据库连接校验失败。请检查 config\runtime.local.json 中的 Prefect PostgreSQL 与驾驶舱 MySQL 配置。"
    }
}

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

if ($ForceRestart) {
    & (Join-Path $PSScriptRoot "stop.ps1") -BackendPort $BackendPort -FrontendPort $FrontendPort
}

Test-RuntimeDatabaseConnections

Write-Host "启动 Prefect Server..."
& (Join-Path $PSScriptRoot "lib\prefect_start.ps1") `
    -Mode server `
    -Detached `
    -ApiUrl $ApiUrl `
    -WorkPool $WorkPool `
    -UseSqliteDebug:$UseSqliteDebug

if (-not (Wait-HttpOk -Url "$($ApiUrl.TrimEnd('/'))/health")) {
    throw "Prefect Server 未在 90 秒内就绪: $ApiUrl"
}

& $PythonExe -m prefect work-pool inspect $WorkPool *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "创建 Work Pool: $WorkPool"
    & $PythonExe -m prefect work-pool create --type process $WorkPool
    if ($LASTEXITCODE -ne 0) {
        throw "创建 Work Pool 失败: $WorkPool"
    }
}

Write-Host "同步 Prefect deployments（仅同步 Cron，不运行 flow）..."
& $PythonExe -m prefect deploy --all --pool $WorkPool
if ($LASTEXITCODE -ne 0) {
    throw "Prefect deployment 同步失败"
}

Write-Host "启动 Prefect Worker..."
& (Join-Path $PSScriptRoot "lib\prefect_start.ps1") `
    -Mode worker `
    -Detached `
    -ApiUrl $ApiUrl `
    -WorkPool $WorkPool `
    -UseSqliteDebug:$UseSqliteDebug

if (-not $SkipWeb) {
    Write-Host "启动管理端..."
    & (Join-Path $PSScriptRoot "lib\start_web.ps1") `
        -Mode both `
        -BackendPort $BackendPort `
        -FrontendPort $FrontendPort `
        -PrefectApiUrl $ApiUrl
}

Write-Host "运行栈已启动。Prefect: $ApiUrl; Work Pool: $WorkPool"
