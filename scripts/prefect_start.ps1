param(
    [ValidateSet("server", "worker", "both")]
    [string]$Mode = "both",
    [string]$ApiUrl = "http://127.0.0.1:4200/api",
    [string]$WorkPool = "default-agent-pool",
    [string]$PrefectHome = "",
    [string]$PythonExe = "",
    [string]$DatabaseUrl = "",
    [switch]$UseSqliteDebug,
    [switch]$Detached
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LocalEnvPath = Join-Path $PSScriptRoot "prefect_env_prod.local.ps1"
if (Test-Path -LiteralPath $LocalEnvPath) {
    . $LocalEnvPath
}
. (Join-Path $PSScriptRoot "dashboard\mysql_env.ps1")
if (-not $PrefectHome) {
    $PrefectHome = Join-Path $RepoRoot "runtime\prefect_home"
}
if (-not $PythonExe) {
    $activeEnvName = if ($env:CONDA_PREFIX) { Split-Path -Leaf $env:CONDA_PREFIX } else { "" }
    if (
        $activeEnvName -eq "auto-notify" -and
        (Test-Path -LiteralPath (Join-Path $env:CONDA_PREFIX "python.exe"))
    ) {
        $PythonExe = Join-Path $env:CONDA_PREFIX "python.exe"
    } else {
        $condaPython = $null
        $condaCmd = Get-Command conda -ErrorAction SilentlyContinue
        if ($condaCmd) {
            $envInfo = (& conda env list 2>$null) | Select-String -Pattern "^\s*auto-notify\s+(.+)$" | Select-Object -First 1
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
    $DatabaseUrl = $env:AUTO_NOTIFY_PREFECT_DEV_DATABASE_URL
    if (-not $DatabaseUrl) {
        $sourceUrl = $env:AUTO_NOTIFY_PREFECT_DATABASE_URL
        if (-not $sourceUrl) {
            throw "Missing Prefect database URL. Configure AUTO_NOTIFY_PREFECT_DATABASE_URL so prefect_dev can be derived."
        }
        if ($sourceUrl -notmatch "/prefect(?:\?.*)?$") {
            throw "AUTO_NOTIFY_PREFECT_DATABASE_URL must end with /prefect so prefect_dev can be derived safely."
        }
        $DatabaseUrl = $sourceUrl -replace "/prefect(\?.*)?$", "/prefect_dev`$1"
    }
}

if (
    -not $UseSqliteDebug -and
    (
        $DatabaseUrl -notlike "postgresql+asyncpg://*" -or
        $DatabaseUrl -notmatch "/prefect_dev(?:\?.*)?$"
    )
) {
    throw "Prefect is locked to the prefect_dev database. Refusing DatabaseUrl that does not end with /prefect_dev."
}

New-Item -ItemType Directory -Force -Path $PrefectHome | Out-Null

$env:PREFECT_HOME = $PrefectHome
$env:PREFECT_API_URL = $ApiUrl
$env:PREFECT_API_DATABASE_CONNECTION_URL = $DatabaseUrl
$env:PREFECT_SERVER_DATABASE_CONNECTION_URL = $DatabaseUrl
$env:PREFECT_API_DATABASE_TIMEOUT = "60"
$env:PREFECT_SERVER_DATABASE_TIMEOUT = "60"
$env:PREFECT_API_SERVICES_SCHEDULER_ENABLED = if ($UseSqliteDebug) { "False" } else { "True" }
$env:PREFECT_API_SERVICES_LATE_RUNS_ENABLED = "False"
$env:PREFECT_SERVER_ANALYTICS_ENABLED = "False"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONNOUSERSITE = "1"

Write-Host "RepoRoot       : $RepoRoot"
Write-Host "PythonExe      : $PythonExe"
Write-Host "PREFECT_HOME   : $($env:PREFECT_HOME)"
Write-Host "PREFECT_API_URL: $($env:PREFECT_API_URL)"
$databaseSource = if ($UseSqliteDebug) { "SQLite debug database" } else { "PostgreSQL database: prefect_dev" }
Write-Host "DatabaseUrl    : $databaseSource"
Write-Host "DebugSqlite    : $UseSqliteDebug"
Write-Host "LateRuns       : $($env:PREFECT_API_SERVICES_LATE_RUNS_ENABLED)"
Write-Host "Mode           : $Mode"
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
`$env:PYTHONNOUSERSITE = '1'
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

$serverArgs = if ($UseSqliteDebug) { "server start --no-services --workers 1" } else { "server start --workers 1" }
$serverCommand = (Get-EnvBootstrap) + "`n& '$PythonExe' -m prefect $serverArgs"
$workerCommand = (Get-EnvBootstrap) + "`n& '$PythonExe' -m prefect worker start --pool '$WorkPool' --type process"

function Pause-DashboardDeployments {
    param([string]$PythonExe)

    $script = @"
import asyncio
from prefect.client.orchestration import get_client
from prefect.states import Cancelled

TARGETS = {
    "dashboard-collection",
    "dashboard-daily",
    "dashboard-monthly",
    "dashboard-indicator-sync",
    "dashboard-v2-partition-maintenance",
}
ACTIVE = {"SCHEDULED", "PENDING", "RUNNING", "LATE", "AWAITINGRETRY", "RETRYING"}

async def main():
    async with get_client() as client:
        deployments = await client.read_deployments()
        target_deployments = {
            deployment.id: deployment
            for deployment in deployments
            if deployment.name in TARGETS
        }
        for deployment in target_deployments.values():
            if not getattr(deployment, "paused", False):
                await client.pause_deployment(deployment.id)
                print(f"paused:{deployment.name}")
        # Pausing a deployment does not cancel runs that the scheduler created
        # before the pause.  Clear those runs before a worker is started,
        # otherwise stale V1 work may execute during V2 cutover preparation.
        for run in await client.read_flow_runs(limit=200):
            deployment = target_deployments.get(run.deployment_id)
            state_type = run.state.type.value if run.state else ""
            if deployment and state_type in ACTIVE:
                await client.set_flow_run_state(
                    run.id,
                    Cancelled(message="启动 Worker 前清理已暂停驾驶舱的遗留队列"),
                    force=True,
                )
                print(f"cancelled:{deployment.name}:{run.id}:{state_type}")

asyncio.run(main())
"@

    & $PythonExe -c $script
    if ($LASTEXITCODE -ne 0) {
        throw "暂停 dashboard deployments 失败"
    }
}

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
        $serverReady = Wait-ForPrefectServer -Url "http://127.0.0.1:4200/api/health"
        if (-not $serverReady) {
            throw "Prefect Server was not ready within 90 seconds. Check the Prefect Server window logs."
        }
        Pause-DashboardDeployments -PythonExe $PythonExe
        Start-DetachedWindow -Title "Prefect Worker" -Command $workerCommand
        Write-Host "Started Server + Worker in two new windows."
        Write-Host "Open UI: http://127.0.0.1:4200"
        if ($UseSqliteDebug) {
            Write-Host "SQLite debug mode: remote PostgreSQL is not used; not recommended for long-running schedules."
        }
    }
}
