param(
    [ValidateSet("backend", "frontend", "both")]
    [string]$Mode = "both",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$PrefectApiUrl = "http://127.0.0.1:4200/api"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "dashboard\mysql_env.ps1")
$PythonExe = ""
$activeEnvName = if ($env:CONDA_PREFIX) { Split-Path -Leaf $env:CONDA_PREFIX } else { "" }
if (
    $activeEnvName -eq "auto-notify" -and
    (Test-Path -LiteralPath (Join-Path $env:CONDA_PREFIX "python.exe"))
) {
    $PythonExe = Join-Path $env:CONDA_PREFIX "python.exe"
} else {
    $condaCmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($condaCmd) {
        $envInfo = (& conda env list 2>$null) | Select-String -Pattern "^\s*auto-notify\s+(?:\*\s+)?(.+)$" | Select-Object -First 1
        if ($envInfo) {
            $candidatePython = Join-Path $envInfo.Matches[0].Groups[1].Value.Trim() "python.exe"
            if (Test-Path -LiteralPath $candidatePython) {
                $PythonExe = $candidatePython
            }
        }
    }
}
if (-not $PythonExe) {
    $PythonExe = "python"
}

function Start-Backend {
    Set-Location $RepoRoot
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONNOUSERSITE = "1"
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

switch ($Mode) {
    "backend" { Start-Backend }
    "frontend" { Start-Frontend }
    "both" {
        $reloadArgs = "--reload-dir backend --reload-dir services --reload-dir models --reload-dir infrastructure --reload-dir utils --reload-dir flows --reload-dir tasks"
        $backendCommand = "cd '$RepoRoot'; `$env:PYTHONUTF8='1'; `$env:PYTHONIOENCODING='utf-8'; `$env:PYTHONNOUSERSITE='1'; `$env:PREFECT_API_URL='$PrefectApiUrl'; & '$PythonExe' -m uvicorn backend.app:app --reload $reloadArgs --host 127.0.0.1 --port $BackendPort"
        $frontendDir = Join-Path $RepoRoot "frontend"
        $frontendCommand = "cd '$frontendDir'; npm run dev -- --host 127.0.0.1 --port $FrontendPort"

        Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCommand
        Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCommand
        Write-Host "已启动后端和前端窗口。"
        Write-Host "后端: http://127.0.0.1:$BackendPort/api/health"
        Write-Host "前端: http://127.0.0.1:$FrontendPort"
        Write-Host "Prefect API: $PrefectApiUrl"
    }
}
