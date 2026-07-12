param(
    [ValidateSet("server", "worker", "both")]
    [string]$Mode = "both",
    [string]$ApiUrl = "",
    [string]$WorkPool = "",
    [string]$PrefectHome = "",
    [string]$PythonExe = "",
    [string]$DatabaseUrl = "",
    [ValidateRange(1, 128)]
    [int]$WorkerLimit = 1,
    [switch]$UseSqliteDebug,
    [switch]$NoWorkerRestart,
    [switch]$Detached
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot "python_env.ps1")
$ScriptsRoot = Split-Path -Parent $PSScriptRoot
$UnifiedLocalEnvPath = Join-Path $ScriptsRoot "environment.local.ps1"
$LegacyLocalEnvPath = Join-Path $ScriptsRoot "prefect_env_prod.local.ps1"
. (Join-Path $PSScriptRoot "runtime_config.ps1")
if (-not (Import-ProjectRuntimeConfig)) {
    if (Test-Path -LiteralPath $UnifiedLocalEnvPath) {
        . $UnifiedLocalEnvPath
    } elseif (Test-Path -LiteralPath $LegacyLocalEnvPath) {
        . $LegacyLocalEnvPath
    }
}
. (Join-Path $ScriptsRoot "tools\dashboard\mysql_env.ps1")
if (-not $ApiUrl) {
    $ApiUrl = $env:PREFECT_API_URL
    if (-not $ApiUrl) {
        $ApiUrl = "http://127.0.0.1:4200/api"
    }
}
if (-not $WorkPool) {
    $WorkPool = $env:PREFECT_WORK_POOL_NAME
    if (-not $WorkPool) {
        $WorkPool = "default-agent-pool"
    }
}
if (-not $PrefectHome) {
    $PrefectHome = Join-Path $RepoRoot "runtime\prefect_home"
}
if (-not $PythonExe) {
    $PythonExe = Get-ProjectPython
}

if ($UseSqliteDebug) {
    $DatabaseUrl = "sqlite+aiosqlite:///" + (($PrefectHome -replace "\\", "/") + "/prefect.db")
}
elseif (-not $DatabaseUrl) {
    $DatabaseUrl = $env:AUTO_NOTIFY_PREFECT_DATABASE_URL
    if (-not $DatabaseUrl) {
        throw "Missing Prefect database URL. Configure AUTO_NOTIFY_PREFECT_DATABASE_URL."
    }
}

if (-not $UseSqliteDebug -and $DatabaseUrl -notlike "postgresql+asyncpg://*") {
    throw "Prefect database URL must use the postgresql+asyncpg scheme."
}

New-Item -ItemType Directory -Force -Path $PrefectHome | Out-Null

$env:PREFECT_HOME = $PrefectHome
$env:PREFECT_API_URL = $ApiUrl
$env:PREFECT_API_DATABASE_CONNECTION_URL = $DatabaseUrl
$env:PREFECT_SERVER_DATABASE_CONNECTION_URL = $DatabaseUrl
$env:PREFECT_API_DATABASE_TIMEOUT = "300"
$env:PREFECT_SERVER_DATABASE_TIMEOUT = "300"
$env:PREFECT_API_SERVICES_SCHEDULER_ENABLED = if ($UseSqliteDebug) { "False" } else { "True" }
$env:PREFECT_API_SERVICES_LATE_RUNS_ENABLED = "False"
$env:PREFECT_SERVER_ANALYTICS_ENABLED = "False"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "RepoRoot       : $RepoRoot"
Write-Host "PythonExe      : $PythonExe"
Write-Host "PREFECT_HOME   : $($env:PREFECT_HOME)"
Write-Host "PREFECT_API_URL: $($env:PREFECT_API_URL)"
$databaseSource = if ($UseSqliteDebug) { "SQLite debug database" } else { "PostgreSQL database from configured URL" }
Write-Host "DatabaseUrl    : $databaseSource"
Write-Host "DebugSqlite    : $UseSqliteDebug"
Write-Host "LateRuns       : $($env:PREFECT_API_SERVICES_LATE_RUNS_ENABLED)"
Write-Host "Mode           : $Mode"
Write-Host "WorkerLimit    : $WorkerLimit"
Write-Host "WorkerRestart  : $($Detached -and -not $NoWorkerRestart)"
Write-Host ""

$PrefectHealthUrl = $ApiUrl.TrimEnd("/") + "/health"

function Start-DetachedWindow {
    param(
        [string]$Title,
        [string]$Command
    )
    $escaped = $Command.Replace('"', '\"')
    Start-Process -FilePath "pwsh" -ArgumentList @(
        "-NoExit",
        "-Command",
        "`$Host.UI.RawUI.WindowTitle='$Title'; $escaped"
    ) | Out-Null
}

function Wait-ForPrefectServer {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 300
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

function Test-PrefectDatabase {
    if ($UseSqliteDebug) {
        return
    }

    Write-Host "检查 Prefect PostgreSQL 连接..."
    $checkScript = @'
import asyncio
import os
import sys

import asyncpg

url = os.environ.get("PREFECT_API_DATABASE_CONNECTION_URL") or os.environ.get("PREFECT_SERVER_DATABASE_CONNECTION_URL")
if not url:
    print("missing_database_url")
    sys.exit(2)

pg_url = url.replace("postgresql+asyncpg://", "postgresql://", 1)

async def main():
    conn = await asyncpg.connect(pg_url, timeout=15)
    try:
        await conn.fetchval("select 1")
    finally:
        await conn.close()

try:
    asyncio.run(main())
except Exception as exc:
    print(f"{type(exc).__name__}: {exc}")
    sys.exit(1)
print("postgres_ok")
'@
    $checkOutput = $checkScript | & $PythonExe -
    $checkExitCode = $LASTEXITCODE
    $checkOutput | ForEach-Object { Write-Host "  $_" }
    if ($checkExitCode -ne 0) {
        throw "Prefect PostgreSQL 连接失败。请先检查 scripts\prefect_env_prod.local.ps1 中的 AUTO_NOTIFY_PREFECT_DATABASE_URL、服务器 5432 端口、数据库服务和网络连通性。"
    }
}

function Get-EnvBootstrap {
@"
`$env:PREFECT_HOME = '$($env:PREFECT_HOME)'
`$env:PREFECT_API_URL = '$($env:PREFECT_API_URL)'
`$env:PREFECT_API_DATABASE_CONNECTION_URL = '$($env:PREFECT_API_DATABASE_CONNECTION_URL)'
`$env:PREFECT_SERVER_DATABASE_CONNECTION_URL = '$($env:PREFECT_SERVER_DATABASE_CONNECTION_URL)'
`$env:PREFECT_API_DATABASE_TIMEOUT = '$($env:PREFECT_API_DATABASE_TIMEOUT)'
`$env:PREFECT_SERVER_DATABASE_TIMEOUT = '$($env:PREFECT_SERVER_DATABASE_TIMEOUT)'
`$env:PREFECT_API_SERVICES_SCHEDULER_ENABLED = '$($env:PREFECT_API_SERVICES_SCHEDULER_ENABLED)'
`$env:PREFECT_API_SERVICES_LATE_RUNS_ENABLED = '$($env:PREFECT_API_SERVICES_LATE_RUNS_ENABLED)'
`$env:PREFECT_SERVER_ANALYTICS_ENABLED = 'False'
`$env:PYTHONUTF8 = '1'
`$env:PYTHONIOENCODING = 'utf-8'
`$env:DASHBOARD_MYSQL_HOST = '$($env:DASHBOARD_MYSQL_HOST)'
`$env:DASHBOARD_MYSQL_PORT = '$($env:DASHBOARD_MYSQL_PORT)'
`$env:DASHBOARD_MYSQL_DATABASE = '$($env:DASHBOARD_MYSQL_DATABASE)'
`$env:DASHBOARD_MYSQL_USER = '$($env:DASHBOARD_MYSQL_USER)'
`$env:DASHBOARD_MYSQL_PASSWORD = '$($env:DASHBOARD_MYSQL_PASSWORD)'
`$env:DASHBOARD_MYSQL_CONNECT_TIMEOUT_SECONDS = '$($env:DASHBOARD_MYSQL_CONNECT_TIMEOUT_SECONDS)'
`$env:DASHBOARD_MYSQL_IO_TIMEOUT_SECONDS = '$($env:DASHBOARD_MYSQL_IO_TIMEOUT_SECONDS)'
`$env:DASHBOARD_MYSQL_POOL_RECYCLE_SECONDS = '$($env:DASHBOARD_MYSQL_POOL_RECYCLE_SECONDS)'
`$env:DASHBOARD_MYSQL_POOL_SIZE = '$($env:DASHBOARD_MYSQL_POOL_SIZE)'
`$env:DASHBOARD_MYSQL_MAX_OVERFLOW = '$($env:DASHBOARD_MYSQL_MAX_OVERFLOW)'
`$env:DASHBOARD_MYSQL_CHARSET = '$($env:DASHBOARD_MYSQL_CHARSET)'
"@
}

$workerArgs = @("worker", "start", "--pool", $WorkPool, "--type", "process", "--limit", $WorkerLimit)

function Get-WorkerCommand {
    param(
        [bool]$Restart
    )

    $startWorker = "& '$PythonExe' -m prefect " + (($workerArgs | ForEach-Object { "'$_'" }) -join " ")
    if (-not $Restart) {
        return (Get-EnvBootstrap) + "`n$startWorker"
    }

    return (Get-EnvBootstrap) + @"

while (`$true) {
    Write-Host "[worker-supervisor] starting Prefect worker at `$(Get-Date -Format o)"
    $startWorker
    `$exitCode = if (`$null -ne `$LASTEXITCODE) { `$LASTEXITCODE } else { 1 }
    Write-Host "[worker-supervisor] worker exited with code `$exitCode at `$(Get-Date -Format o); restarting in 30s"
    Start-Sleep -Seconds 30
}
"@
}

function Assert-NoOnlineWorker {
    $checkScript = @'
import asyncio
import sys

from prefect.client.orchestration import get_client


async def has_online_worker(work_pool_name: str) -> bool:
    async with get_client() as client:
        workers = await client.read_workers_for_work_pool(work_pool_name)
    return any(
        getattr(worker.status, "value", worker.status) == "ONLINE"
        for worker in workers
    )


if asyncio.run(has_online_worker(sys.argv[1])):
    raise SystemExit(3)
'@

    $checkScript | & $PythonExe - $WorkPool
    $checkExitCode = $LASTEXITCODE
    if ($checkExitCode -eq 3) {
        throw "Work Pool 已有在线 Worker，拒绝重复启动: $WorkPool"
    }
    if ($checkExitCode -ne 0) {
        throw "检查 Work Pool Worker 状态失败: $WorkPool"
    }
}

Test-PrefectDatabase

$serverArgs = if ($UseSqliteDebug) { "server start --no-services --workers 1" } else { "server start --workers 1" }
$serverCommand = (Get-EnvBootstrap) + "`n& '$PythonExe' -m prefect $serverArgs"
$workerCommand = Get-WorkerCommand -Restart ($Detached -and -not $NoWorkerRestart)

switch ($Mode) {
    "server" {
        if ($Detached) {
            Start-DetachedWindow -Title "Prefect Server" -Command $serverCommand
            Write-Host "Started Prefect Server in a new window."
        } else {
            & $PythonExe -m prefect @($serverArgs -split ' ')
        }
    }
    "worker" {
        $serverReady = Wait-ForPrefectServer -Url $PrefectHealthUrl
        if (-not $serverReady) {
            throw "Prefect Server was not ready within 90 seconds. Refusing to start Worker."
        }
        Assert-NoOnlineWorker
        if ($Detached) {
            Start-DetachedWindow -Title "Prefect Worker" -Command $workerCommand
            Write-Host "Started Prefect Worker in a new window."
        } else {
            & $PythonExe -m prefect @workerArgs
        }
    }
    "both" {
        Start-DetachedWindow -Title "Prefect Server" -Command $serverCommand
        $serverReady = Wait-ForPrefectServer -Url $PrefectHealthUrl
        if (-not $serverReady) {
            throw "Prefect Server was not ready within 300 seconds. Check the Prefect Server window logs."
        }
        Assert-NoOnlineWorker
        Start-DetachedWindow -Title "Prefect Worker" -Command $workerCommand
        Write-Host "Started Server + Worker in two new windows."
        Write-Host "Open UI: http://127.0.0.1:4200"
        if ($UseSqliteDebug) {
            Write-Host "SQLite debug mode: remote PostgreSQL is not used; not recommended for long-running schedules."
        }
    }
}
