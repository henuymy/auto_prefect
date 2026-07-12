param(
    [ValidateSet("backend", "frontend", "both")]
    [string]$Mode = "both",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$PrefectApiUrl = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot "python_env.ps1")
. (Join-Path $PSScriptRoot "runtime_config.ps1")
. (Join-Path $PSScriptRoot "process_registry.ps1")
Import-ProjectRuntimeConfig | Out-Null
. (Join-Path (Split-Path -Parent $PSScriptRoot) "tools\dashboard\mysql_env.ps1")
if (-not $PrefectApiUrl) {
    $PrefectApiUrl = $env:PREFECT_API_URL
    if (-not $PrefectApiUrl) {
        $PrefectApiUrl = "http://127.0.0.1:4200/api"
    }
}
$PythonExe = Get-ProjectPython

function Start-Backend {
    Set-Location $RepoRoot
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:PREFECT_API_URL = $PrefectApiUrl
    & $PythonExe -m uvicorn backend.app:app --reload `
        --reload-dir backend `
        --reload-dir services `
        --reload-dir models `
        --reload-dir infrastructure `
        --reload-dir utils `
        --reload-dir flows `
        --reload-dir tasks `
        --host 127.0.0.1 --port $BackendPort
}

function Start-Frontend {
    Set-Location (Join-Path $RepoRoot "frontend")
    $env:VITE_API_BASE = ""
    npm run dev -- --host 127.0.0.1 --port $FrontendPort
}

function Start-ManagedWebProcess {
    param(
        [string]$Name,
        [string]$Command,
        [string]$RegisteredCommand
    )

    Assert-ManagedProcessAvailable -Name $Name
    $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
    $process = Start-Process -FilePath "powershell" -ArgumentList @(
        "-NoProfile",
        "-EncodedCommand",
        $encodedCommand
    ) -PassThru -WindowStyle Hidden
    try {
        Register-ManagedProcess -Name $Name -Process $process -Command $RegisteredCommand | Out-Null
    } catch {
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
        throw
    }
}

switch ($Mode) {
    "backend" { Start-Backend }
    "frontend" { Start-Frontend }
    "both" {
        $reloadArgs = "--reload-dir backend --reload-dir services --reload-dir models --reload-dir infrastructure --reload-dir utils --reload-dir flows --reload-dir tasks"
        $backendCommand = "cd '$RepoRoot'; `$env:PYTHONUTF8='1'; `$env:PYTHONIOENCODING='utf-8'; `$env:PREFECT_API_URL='$PrefectApiUrl'; & '$PythonExe' -m uvicorn backend.app:app --reload $reloadArgs --host 127.0.0.1 --port $BackendPort"
        $frontendDir = Join-Path $RepoRoot "frontend"
        $frontendCommand = "cd '$frontendDir'; npm run dev -- --host 127.0.0.1 --port $FrontendPort"

        Start-ManagedWebProcess `
            -Name "web-backend" `
            -Command $backendCommand `
            -RegisteredCommand "uvicorn backend.app:app --host 127.0.0.1 --port $BackendPort"
        Start-ManagedWebProcess `
            -Name "web-frontend" `
            -Command $frontendCommand `
            -RegisteredCommand "npm run dev -- --host 127.0.0.1 --port $FrontendPort"
        Write-Host "已启动后端和前端窗口。"
        Write-Host "后端: http://127.0.0.1:$BackendPort/api/health"
        Write-Host "前端: http://127.0.0.1:$FrontendPort"
        Write-Host "Prefect API: $PrefectApiUrl"
    }
}
