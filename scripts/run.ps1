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
. (Join-Path $PSScriptRoot "lib\process_registry.ps1")
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
$PrefectHome = Join-Path $env:AUTO_NOTIFY_RUNTIME_ROOT "prefect\prefect_home"
$StartupClaim = Enter-StartupClaim

try {
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

function Wait-WorkerOnline {
    param(
        [string]$WorkPool,
        [int]$TimeoutSeconds = 90
    )

    $waitScript = @'
import asyncio
import sys
import time

from prefect.client.orchestration import get_client


async def wait_for_online_worker(work_pool_name: str, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    async with get_client() as client:
        while time.monotonic() < deadline:
            workers = await client.read_workers_for_work_pool(work_pool_name)
            if any(
                getattr(worker.status, "value", worker.status) == "ONLINE"
                for worker in workers
            ):
                return True
            await asyncio.sleep(2)
    return False


if not asyncio.run(wait_for_online_worker(sys.argv[1], int(sys.argv[2]))):
    raise SystemExit(2)
'@

    $waitScript | & $PythonExe - $WorkPool $TimeoutSeconds
    if ($LASTEXITCODE -ne 0) {
        throw "Session Worker 未在 $TimeoutSeconds 秒内上线: $WorkPool"
    }
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
    -PrefectHome $PrefectHome `
    -UseSqliteDebug:$UseSqliteDebug

if (-not (Wait-HttpOk -Url "$($ApiUrl.TrimEnd('/'))/health")) {
    throw "Prefect Server 未在 90 秒内就绪: $ApiUrl"
}

$Pools = @(
    [pscustomobject]@{ Name = $env:PREFECT_SESSION_POOL_NAME; Limit = [int]$env:PREFECT_SESSION_POOL_LIMIT },
    [pscustomobject]@{ Name = $env:PREFECT_DASHBOARD_POOL_NAME; Limit = [int]$env:PREFECT_DASHBOARD_POOL_LIMIT },
    [pscustomobject]@{ Name = $env:PREFECT_NOTIFY_POOL_NAME; Limit = [int]$env:PREFECT_NOTIFY_POOL_LIMIT }
)

foreach ($pool in $Pools) {
    & $PythonExe -m prefect work-pool inspect $pool.Name *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "创建 Work Pool: $($pool.Name)"
        & $PythonExe -m prefect work-pool create --type process $pool.Name
        if ($LASTEXITCODE -ne 0) {
            throw "创建 Work Pool 失败: $($pool.Name)"
        }
    }

    & $PythonExe -m prefect work-pool set-concurrency-limit $pool.Name $pool.Limit
    if ($LASTEXITCODE -ne 0) {
        throw "设置 Work Pool 并发上限失败: $($pool.Name)"
    }
}

Write-Host "清理 Prefect 启动队列..."
$ReconcileScript = Join-Path $PSScriptRoot "lib\prefect_startup_reconcile.py"
$ReconcileArgs = @(
    "--notify-grace-seconds",
    ([int]$env:AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS),
    "--notify-work-pool",
    $env:PREFECT_NOTIFY_POOL_NAME
)
if ($ForceRestart) {
    $ReconcileArgs += "--cancel-in-flight"
}
$reconcileOutput = @(& $PythonExe $ReconcileScript @ReconcileArgs)
$reconcileExitCode = $LASTEXITCODE
$reconcileOutput | ForEach-Object { Write-Host $_ }
if ($reconcileExitCode -ne 0) {
    throw "Prefect 启动队列清理失败，未启动 Worker"
}
$LegacyNotifyRuns = @(
    $reconcileOutput |
        Where-Object { $_ -like "legacy_notify_run:*" } |
        ForEach-Object { $_.Substring("legacy_notify_run:".Length) }
)
if ($LegacyNotifyRuns.Count -gt 0) {
    $runSummary = $LegacyNotifyRuns -join "; "
    throw "旧 Notify Pool 仍有保留 Run，Prefect 3.7 不支持安全改派单个排队 Run，已拒绝启动避免遗漏或执行无关工作。请先用旧 Pool Worker 排空或取消这些 Run。Prefect Server 已注册；处理后请执行 scripts\stop.ps1 再重新运行 scripts\run.ps1，或直接执行 scripts\run.ps1 -ForceRestart。Runs: $runSummary"
}

Write-Host "启动 Session Prefect Worker..."
& (Join-Path $PSScriptRoot "lib\prefect_start.ps1") `
    -Mode worker `
    -Detached `
    -ApiUrl $ApiUrl `
    -WorkPool $env:PREFECT_SESSION_POOL_NAME `
    -PrefectHome $PrefectHome `
    -WorkerLimit ([int]$env:PREFECT_SESSION_POOL_LIMIT) `
    -UseSqliteDebug:$UseSqliteDebug

Write-Host "启动 Dashboard Prefect Worker..."
& (Join-Path $PSScriptRoot "lib\prefect_start.ps1") `
    -Mode worker `
    -Detached `
    -ApiUrl $ApiUrl `
    -WorkPool $env:PREFECT_DASHBOARD_POOL_NAME `
    -PrefectHome $PrefectHome `
    -WorkerLimit ([int]$env:PREFECT_DASHBOARD_POOL_LIMIT) `
    -UseSqliteDebug:$UseSqliteDebug

Write-Host "启动 Notify Prefect Worker..."
& (Join-Path $PSScriptRoot "lib\prefect_start.ps1") `
    -Mode worker `
    -Detached `
    -ApiUrl $ApiUrl `
    -WorkPool $env:PREFECT_NOTIFY_POOL_NAME `
    -PrefectHome $PrefectHome `
    -WorkerLimit ([int]$env:PREFECT_NOTIFY_POOL_LIMIT) `
    -UseSqliteDebug:$UseSqliteDebug

Wait-WorkerOnline -WorkPool $env:PREFECT_SESSION_POOL_NAME

Write-Host "触发首次 Session Keeper 检查..."
& $PythonExe -m prefect deployment run "session-keeper-flow/session-keeper"
if ($LASTEXITCODE -ne 0) {
    throw "首次 Session Keeper Flow 提交失败"
}

if (-not $SkipWeb) {
    Write-Host "启动管理端..."
    & (Join-Path $PSScriptRoot "lib\start_web.ps1") `
        -Mode both `
        -BackendPort $BackendPort `
        -FrontendPort $FrontendPort `
        -PrefectApiUrl $ApiUrl
}

Write-Host "运行栈已启动。Prefect: $ApiUrl; Work Pools: $($Pools.Name -join ', ')"
} finally {
    Exit-StartupClaim -Claim $StartupClaim
}
