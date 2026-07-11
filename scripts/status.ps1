param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Continue"

. (Join-Path $PSScriptRoot "lib\runtime_config.ps1")
if (-not (Import-ProjectRuntimeConfig)) {
    . (Join-Path $PSScriptRoot "dev\env.ps1")
}

$urls = @(
    "$($env:PREFECT_API_URL.TrimEnd('/'))/health",
    "http://127.0.0.1:$BackendPort/api/health",
    "http://127.0.0.1:$FrontendPort"
)
foreach ($url in $urls) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
        Write-Host "[OK]   $url -> HTTP $($response.StatusCode)"
    } catch {
        Write-Host "[STOP] $url"
    }
}

try {
    $pool = Invoke-RestMethod -Uri "$($env:PREFECT_API_URL.TrimEnd('/'))/work_pools/$($env:PREFECT_WORK_POOL_NAME)" -TimeoutSec 5
    Write-Host "Work Pool: $($pool.name) [$($pool.status)]"
} catch {
    Write-Host "无法读取 Work Pool: $($_.Exception.Message)"
}
