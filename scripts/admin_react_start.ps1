param(
    [ValidateSet("backend", "frontend", "both")]
    [string]$Mode = "both",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = if ($env:CONDA_PREFIX) { Join-Path $env:CONDA_PREFIX "python.exe" } else { "python" }

function Start-Backend {
    Set-Location $RepoRoot
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    & $PythonExe -m uvicorn backend.app:app --reload --host 127.0.0.1 --port $BackendPort
}

function Start-Frontend {
    Set-Location (Join-Path $RepoRoot "admin_react")
    $env:VITE_API_BASE = ""
    npm run dev -- --host 127.0.0.1 --port $FrontendPort
}

switch ($Mode) {
    "backend" { Start-Backend }
    "frontend" { Start-Frontend }
    "both" {
        $backendCommand = "cd '$RepoRoot'; `$env:PYTHONUTF8='1'; `$env:PYTHONIOENCODING='utf-8'; & '$PythonExe' -m uvicorn backend.app:app --reload --host 127.0.0.1 --port $BackendPort"
        $frontendDir = Join-Path $RepoRoot "admin_react"
        $frontendCommand = "cd '$frontendDir'; npm run dev -- --host 127.0.0.1 --port $FrontendPort"

        Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCommand
        Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCommand
        Write-Host "已启动后端和前端窗口。"
        Write-Host "后端: http://127.0.0.1:$BackendPort/api/health"
        Write-Host "前端: http://127.0.0.1:$FrontendPort"
    }
}
