param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Continue"

. (Join-Path $PSScriptRoot "lib\runtime_config.ps1")
if (-not (Import-ProjectRuntimeConfig)) {
    . (Join-Path $PSScriptRoot "dev\env.ps1")
}
. (Join-Path $PSScriptRoot "lib\process_registry.ps1")
. (Join-Path $PSScriptRoot "lib\python_env.ps1")
$PythonExe = Get-ProjectPython

function Write-HttpStatus {
    param([string]$Label, [string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        Write-Host "${Label}: available (HTTP $($response.StatusCode))"
    } catch {
        Write-Host "${Label}: unavailable"
    }
}

function Write-TcpStatus {
    param([string]$Label, [string]$HostName, [int]$Port)
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $task = $client.ConnectAsync($HostName, $Port)
            if (-not $task.Wait(3000) -or -not $client.Connected) {
                throw "connection failed"
            }
            Write-Host "${Label}: available"
        } finally {
            $client.Dispose()
        }
    } catch {
        Write-Host "${Label}: unavailable"
    }
}

function Write-PortStatus {
    param([int]$Port)
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $state = if ($null -ne $listener) { "listening" } else { "closed" }
    Write-Host "Port ${Port}: $state"
}

function Write-ManagedProcessStatus {
    param([string]$Name)
    $record = Get-ManagedProcessRecord -Name $Name
    if ($null -eq $record) {
        Write-Host "${Name}: unregistered"
    } elseif (Test-ManagedProcessRecord -Record $record) {
        Write-Host "${Name}: running (PID $($record.pid))"
    } else {
        Write-Host "${Name}: stale registration"
    }
}

function Get-PrefectPoolStatuses {
    param([string[]]$Names)

    $statusScript = @'
import asyncio
import json
import sys

from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import (
    FlowRunFilter,
    FlowRunFilterState,
    FlowRunFilterStateType,
    WorkPoolFilter,
    WorkPoolFilterName,
    WorkerFilter,
    WorkerFilterStatus,
)
from prefect.states import StateType


async def count_runs(client, pool_name, state_types):
    count = 0
    offset = 0
    while True:
        runs = await client.read_flow_runs(
            flow_run_filter=FlowRunFilter(
                state=FlowRunFilterState(
                    type=FlowRunFilterStateType(any_=state_types)
                )
            ),
            work_pool_filter=WorkPoolFilter(
                name=WorkPoolFilterName(any_=[pool_name])
            ),
            limit=200,
            offset=offset,
        )
        count += len(runs)
        if len(runs) < 200:
            return count
        offset += len(runs)


async def main(pool_names):
    result = {}
    async with get_client() as client:
        for pool_name in pool_names:
            workers = await client.read_workers_for_work_pool(
                pool_name,
                worker_filter=WorkerFilter(
                    status=WorkerFilterStatus(any_=["ONLINE"])
                ),
                limit=200,
            )
            result[pool_name] = {
                "online_workers": len(workers),
                "running": await count_runs(client, pool_name, [StateType.RUNNING]),
                "queued": await count_runs(
                    client, pool_name, [StateType.SCHEDULED, StateType.PENDING]
                ),
            }
    return result


print(json.dumps(asyncio.run(main(sys.argv[1:])), ensure_ascii=True))
'@

    try {
        $output = $statusScript | & $PythonExe - @Names 2>$null
        if ($LASTEXITCODE -ne 0) {
            return $null
        }
        return ($output | Select-Object -Last 1 | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return $null
    }
}

function Write-SessionState {
    $path = Join-Path $env:AUTO_NOTIFY_RUNTIME_ROOT "session\session_state.json"
    try {
        $state = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $verifiedAt = [DateTimeOffset]::Parse([string]$state.verified_at)
        $ageSeconds = [math]::Max(0, [math]::Floor(([DateTimeOffset]::Now - $verifiedAt).TotalSeconds))
        $health = if ($state.healthy -eq $true) { "healthy" } else { "unhealthy" }
        Write-Host "Session state: $health / verified_at=$($verifiedAt.ToString('o')) / age_seconds=$ageSeconds"
    } catch {
        Write-Host "Session state: unavailable / verified_at=unknown / age_seconds=unknown"
    }
}

function Write-LockStatus {
    param([string]$FileName)
    $path = Join-Path $env:AUTO_NOTIFY_RUNTIME_ROOT "locks\$FileName"
    try {
        $metadata = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $ownerPid = [int]$metadata.pid
        $acquiredAt = [DateTimeOffset]::Parse([string]$metadata.acquired_at)
        $heldSeconds = [math]::Max(0, [math]::Floor(([DateTimeOffset]::Now - $acquiredAt).TotalSeconds))
        Write-Host "${FileName}: owner PID=$ownerPid / held seconds=$heldSeconds"
    } catch {
        Write-Host "${FileName}: not held / owner PID=none / held seconds=0"
    }
}

foreach ($name in @(
    "prefect-server",
    "prefect-worker-session",
    "prefect-worker-dashboard",
    "prefect-worker-notify",
    "web-backend",
    "web-frontend"
)) {
    Write-ManagedProcessStatus -Name $name
}

Write-HttpStatus -Label "Prefect API" -Url "$($env:PREFECT_API_URL.TrimEnd('/'))/health"
Write-HttpStatus -Label "Backend API" -Url "http://127.0.0.1:$BackendPort/api/health"
Write-HttpStatus -Label "Frontend" -Url "http://127.0.0.1:$FrontendPort"

try {
    $postgresUri = [uri]$env:AUTO_NOTIFY_PREFECT_DATABASE_URL
    $postgresPort = if ($postgresUri.Port -gt 0) { $postgresUri.Port } else { 5432 }
    Write-TcpStatus -Label "PostgreSQL" -HostName $postgresUri.Host -Port $postgresPort
} catch {
    Write-Host "PostgreSQL: unavailable"
}
Write-TcpStatus -Label "MySQL" -HostName $env:DASHBOARD_MYSQL_HOST -Port ([int]$env:DASHBOARD_MYSQL_PORT)

foreach ($port in @(4200, $BackendPort, $FrontendPort) | Select-Object -Unique) {
    Write-PortStatus -Port $port
}

$pools = @(
    [pscustomobject]@{ Name = "windows-session-pool"; Limit = 1; LimitText = "limit=1" },
    [pscustomobject]@{ Name = "windows-dashboard-pool"; Limit = 4; LimitText = "limit=4" },
    [pscustomobject]@{ Name = "windows-notify-pool"; Limit = 6; LimitText = "limit=6" }
)
$poolStatuses = Get-PrefectPoolStatuses -Names @($pools.Name)
foreach ($pool in $pools) {
    $counts = if ($null -ne $poolStatuses) { $poolStatuses.($pool.Name) } else { $null }
    if ($null -eq $counts) {
        Write-Host "$($pool.Name): online workers=unavailable / running=unavailable / queued=unavailable / $($pool.LimitText)"
    } else {
        Write-Host "$($pool.Name): online workers=$($counts.online_workers) / running=$($counts.running) / queued=$($counts.queued) / $($pool.LimitText)"
    }
}

Write-SessionState
Write-LockStatus -FileName "login.lock"
Write-LockStatus -FileName "excel_com.lock"

$runtimeRoot = $env:AUTO_NOTIFY_RUNTIME_ROOT
try {
    $driveRoot = [System.IO.Path]::GetPathRoot($runtimeRoot)
    $driveName = $driveRoot.TrimEnd('\').TrimEnd(':')
    $drive = Get-PSDrive -Name $driveName -ErrorAction Stop
    $freeGb = [math]::Round($drive.Free / 1GB, 2)
    Write-Host "Disk free: $runtimeRoot = $freeGb GB"
} catch {
    Write-Host "Disk free: $runtimeRoot = unavailable"
}
