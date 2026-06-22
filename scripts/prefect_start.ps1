param(
    [ValidateSet("server", "worker", "both")]
    [string]$Mode = "both",
    [string]$ApiUrl = "http://127.0.0.1:4200/api",
    [string]$WorkPool = "default-agent-pool",
    [string]$PrefectHome = "",
    [string]$PythonExe = "",
    [string]$DatabaseUrl = "",
    [switch]$UseSqliteDebug,
    [switch]$NoWorkerRestart,
    [switch]$Detached
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LocalEnvPath = Join-Path $PSScriptRoot "prefect_env_prod.local.ps1"
if (Test-Path -LiteralPath $LocalEnvPath) {
    . $LocalEnvPath
}
if (-not $PrefectHome) {
    $PrefectHome = Join-Path $RepoRoot "runtime\prefect_home"
}
if (-not $PythonExe) {
    if ($env:CONDA_PREFIX -and (Test-Path -LiteralPath (Join-Path $env:CONDA_PREFIX "python.exe"))) {
        $PythonExe = Join-Path $env:CONDA_PREFIX "python.exe"
    } else {
        $condaPython = $null
        $condaCmd = Get-Command conda -ErrorAction SilentlyContinue
        if ($condaCmd) {
            $envInfo = (& conda env list 2>$null) | Select-String -Pattern "^\s*auto-notify\s+(?:\*\s+)?(.+)$" | Select-Object -First 1
            if ($envInfo) {
                $condaPython = Join-Path $envInfo.Matches[0].Groups[1].Value.Trim() "python.exe"
            }
        }
        if ($condaPython -and (Test-Path -LiteralPath $condaPython)) {
            $PythonExe = $condaPython
        } else {
            $PythonExe = Join-Path $env:USERPROFILE ".conda\envs\auto-notify\python.exe"
        }
    }
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = "python"
}

if ($UseSqliteDebug) {
    $DatabaseUrl = "sqlite+aiosqlite:///" + (($PrefectHome -replace "\\", "/") + "/prefect.db")
}
elseif (-not $DatabaseUrl) {
    $DatabaseUrl = $env:AUTO_NOTIFY_PREFECT_DATABASE_URL
    if (-not $DatabaseUrl) {
        throw "Missing Prefect database URL. Pass -DatabaseUrl or set `$env:AUTO_NOTIFY_PREFECT_DATABASE_URL in scripts\prefect_env_prod.local.ps1"
    }
}
elseif ($DatabaseUrl -notlike "postgresql+asyncpg://*") {
    throw "DatabaseUrl must be a PostgreSQL asyncpg URL, for example postgresql+asyncpg://user:password@host:5432/prefect"
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
$databaseSource = if ($UseSqliteDebug) { "SQLite debug database" } else { "loaded from parameter or local config" }
Write-Host "DatabaseUrl    : $databaseSource"
Write-Host "DebugSqlite    : $UseSqliteDebug"
Write-Host "LateRuns       : $($env:PREFECT_API_SERVICES_LATE_RUNS_ENABLED)"
Write-Host "Mode           : $Mode"
Write-Host "WorkerRestart  : $($Detached -and -not $NoWorkerRestart)"
Write-Host ""

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
"@
}

function Get-WorkerCommand {
    param(
        [bool]$Restart
    )

    $startWorker = "& '$PythonExe' -m prefect worker start --pool '$WorkPool' --type process"
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
        if ($Detached) {
            Start-DetachedWindow -Title "Prefect Worker" -Command $workerCommand
            Write-Host "Started Prefect Worker in a new window."
        } else {
            & $PythonExe -m prefect worker start --pool $WorkPool --type process
        }
    }
    "both" {
        Start-DetachedWindow -Title "Prefect Server" -Command $serverCommand
        $serverReady = Wait-ForPrefectServer -Url "http://127.0.0.1:4200/api/health" -TimeoutSeconds 300
        if (-not $serverReady) {
            throw "Prefect Server was not ready within 300 seconds. Check the Prefect Server window logs."
        }

        Write-Host "清除积压的 Pending/Scheduled flow runs..."
        try {
            & $PythonExe -c "
import asyncio
from datetime import datetime, timezone
from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterExpectedStartTime, FlowRunFilterState, FlowRunFilterStateType
from prefect.client.schemas.objects import StateType
from prefect.states import Cancelled

async def cancel_pending():
    client = get_client()
    now = datetime.now(timezone.utc)
    # 只清理预计开始时间早于当前时间的 Pending/Scheduled flow runs，保留未来定时任务
    state_filter = FlowRunFilter(
        state=FlowRunFilterState(type=FlowRunFilterStateType(any_=[StateType.PENDING, StateType.SCHEDULED])),
        expected_start_time=FlowRunFilterExpectedStartTime(before_=now),
    )
    runs = await client.read_flow_runs(flow_run_filter=state_filter)
    if not runs:
        print('  没有当前时间之前的积压 flow run')
        return
    cancelled_state = Cancelled(message='重启时自动取消当前时间之前的积压任务')
    for run in runs:
        await client.set_flow_run_state(run.id, cancelled_state)
        print(f'  已取消: {run.id} ({run.name}) expected_start_time={run.expected_start_time}')
    print(f'  共取消 {len(runs)} 个 flow run')

asyncio.run(cancel_pending())
" 2>&1 | ForEach-Object { Write-Host "  $_" }
        } catch {
            Write-Host "  [WARN] 清除积压 flow run 时出错: $_" -ForegroundColor Yellow
        }

        Start-DetachedWindow -Title "Prefect Worker" -Command $workerCommand
        Write-Host "Started Server + Worker in two new windows."
        Write-Host "Open UI: http://127.0.0.1:4200"
        if ($UseSqliteDebug) {
            Write-Host "SQLite debug mode: remote PostgreSQL is not used; not recommended for long-running schedules."
        }
    }
}
