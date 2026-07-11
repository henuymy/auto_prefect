param(
    [switch]$ForceRestart,
    [switch]$SkipAdmin,
    [switch]$ClearScheduledBacklog
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "env.ps1")

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

$ports = @(4200)
if (-not $SkipAdmin) {
    $ports += @($DevBackendPort, $DevFrontendPort)
}
$busyPorts = @(
    foreach ($port in ($ports | Select-Object -Unique)) {
        Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    }
)

if ($busyPorts.Count -gt 0) {
    if ($ForceRestart) {
        & (Join-Path $PSScriptRoot "stop.ps1")
        Start-Sleep -Seconds 3
    } else {
        $busyPorts |
            Select-Object LocalAddress, LocalPort, OwningProcess |
            Format-Table -AutoSize |
            Out-String |
            Write-Host
        throw "开发端口已被占用。请先运行 scripts\dev\stop.ps1，或使用 -ForceRestart。"
    }
}

Write-Host "启动 prefect_dev Server..."
& (Join-Path $ScriptsDir "prefect_start.ps1") `
    -Mode server `
    -Detached `
    -DatabaseUrl $env:AUTO_NOTIFY_PREFECT_DEV_DATABASE_URL `
    -WorkPool $DevWorkPool

if (-not (Wait-HttpOk -Url "$($env:PREFECT_API_URL)/health")) {
    throw "prefect_dev Server 未在 90 秒内就绪"
}

if ($ClearScheduledBacklog) {
    Write-Host "清理 prefect_dev 历史 SCHEDULED flow runs..."
    & (Join-Path $PSScriptRoot "clear_scheduled_backlog.ps1") `
        -PrefectApiUrl $env:PREFECT_API_URL `
        -Execute
}

& $DevPythonExe -m prefect work-pool inspect $DevWorkPool *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "创建开发 Work Pool: $DevWorkPool"
    & $DevPythonExe -m prefect work-pool create --type process $DevWorkPool
    if ($LASTEXITCODE -ne 0) {
        throw "创建开发 Work Pool 失败: $DevWorkPool"
    }
}

Write-Host "启动 $DevWorkPool Worker..."
& (Join-Path $ScriptsDir "prefect_start.ps1") `
    -Mode worker `
    -Detached `
    -DatabaseUrl $env:AUTO_NOTIFY_PREFECT_DEV_DATABASE_URL `
    -WorkPool $DevWorkPool

if (-not $SkipAdmin) {
    Write-Host "启动开发管理端..."
    & (Join-Path $ScriptsDir "start_web.ps1") `
        -Mode both `
        -BackendPort $DevBackendPort `
        -FrontendPort $DevFrontendPort `
        -PrefectApiUrl $env:PREFECT_API_URL
}

Write-Host ""
Write-Host "开发环境已启动"
Write-Host "Prefect UI : http://127.0.0.1:4200"
if (-not $SkipAdmin) {
    Write-Host "Backend    : http://127.0.0.1:$DevBackendPort/api/health"
    Write-Host "Frontend   : http://127.0.0.1:$DevFrontendPort"
}
Write-Host "Database   : prefect_dev"
Write-Host "Work Pool  : $DevWorkPool"
