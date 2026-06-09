$ErrorActionPreference = "Continue"

. (Join-Path $PSScriptRoot "env.ps1")

$urls = @(
    "http://127.0.0.1:4200/api/health",
    "http://127.0.0.1:$DevBackendPort/api/health",
    "http://127.0.0.1:$DevFrontendPort"
)

foreach ($url in $urls) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
        Write-Host "[OK]   $url -> HTTP $($response.StatusCode)"
    } catch {
        Write-Host "[STOP] $url"
    }
}

Write-Host ""
try {
    $deployments = Invoke-RestMethod `
        -Uri "$($env:PREFECT_API_URL)/deployments/filter" `
        -Method Post `
        -ContentType "application/json" `
        -Body "{}" `
        -TimeoutSec 5
    Write-Host "开发 Deployment 数量: $(@($deployments).Count)"
    foreach ($deployment in @($deployments)) {
        Write-Host "  - $($deployment.name)"
    }
} catch {
    Write-Host "无法读取开发 Deployment: $($_.Exception.Message)"
}

try {
    $pool = Invoke-RestMethod `
        -Uri "$($env:PREFECT_API_URL)/work_pools/$DevWorkPool" `
        -Method Get `
        -TimeoutSec 5
    Write-Host "开发 Work Pool: $($pool.name) [$($pool.status)]"
} catch {
    Write-Host "无法读取开发 Work Pool: $($_.Exception.Message)"
}
