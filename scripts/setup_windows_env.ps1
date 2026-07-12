param(
    [string]$PipIndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple",
    [string]$PipTrustedHost = "pypi.tuna.tsinghua.edu.cn",
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "lib\python_env.ps1")
. (Join-Path $PSScriptRoot "lib\runtime_state_migration.ps1")
$SharedRuntimeRoot = "C:\AutoNotifyRuntime"
$RuntimeDirs = @(
    "runtime",
    "runtime\cookies",
    "runtime\flow",
    "runtime\prefect_home",
    "runtime\report_downloader",
    "runtime\report_downloader\downloads",
    "runtime\report_compare",
    "runtime\template_updater"
)
$SharedRuntimeDirs = @(
    "locks",
    "session",
    "cookies",
    "browser_session\edge_profile_auto_login",
    "prefect\prefect_home",
    "logs",
    "temp",
    "processes"
)

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-EdgeExecutable {
    $candidates = @(
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path -LiteralPath $path) {
            return $path
        }
    }
    return $null
}

$PythonExe = Get-ProjectPython

Write-Step "安装 Python 依赖"
& $PythonExe -m pip install --upgrade pip setuptools wheel -i $PipIndexUrl --trusted-host $PipTrustedHost
& $PythonExe -m pip install -r (Join-Path $RepoRoot "requirements-dev.lock") -i $PipIndexUrl --trusted-host $PipTrustedHost

Write-Step "初始化运行目录"
$migrationResults = @(Invoke-RuntimeStateMigration -RepoRoot $RepoRoot -RuntimeRoot $SharedRuntimeRoot)
foreach ($migration in $migrationResults) {
    Write-Host "Runtime migration: $($migration.name) / $($migration.status) / $($migration.target)"
}
foreach ($dir in $RuntimeDirs) {
    New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot $dir) | Out-Null
}
foreach ($dir in $SharedRuntimeDirs) {
    New-Item -ItemType Directory -Force -Path (Join-Path $SharedRuntimeRoot $dir) | Out-Null
}

Write-Step "检查本机组件"
$edgePath = Find-EdgeExecutable
if ($edgePath) {
    $edgeVersion = (& $edgePath --version) 2>$null
    Write-Host "Microsoft Edge: $edgeVersion"
} else {
    Write-Warning "未检测到 Microsoft Edge。自动登录功能将无法使用。"
}

try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Quit()
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($excel) | Out-Null
    Write-Host "Microsoft Excel COM: 可用"
} catch {
    Write-Warning "未检测到可用的 Excel COM。比对/模板更新/截图相关功能将无法使用。"
}

if (-not $SkipSmokeTest) {
    Write-Step "执行基础自检"
    & $PythonExe -c "import prefect, requests, selenium, fastapi, uvicorn, asyncpg, sqlalchemy, pymysql, alembic; import services.login_service; import services.session_manager; import infrastructure.dashboard_mysql; print('smoke_ok')"
}

Write-Step "完成"
Write-Host "Python 路径    : $PythonExe"
Write-Host "项目目录      : $RepoRoot"
Write-Host "共享运行目录  : $SharedRuntimeRoot"
Write-Host "PIP 镜像源    : $PipIndexUrl"
Write-Host ""
Write-Host "后续常用命令：" -ForegroundColor Green
Write-Host "1. 初始化环境: pwsh -File scripts/setup_windows_env.ps1"
Write-Host "2. 启动系统:   pwsh -File scripts/run.ps1"
Write-Host "3. 查看状态:   pwsh -File scripts/status.ps1"
Write-Host "4. 停止系统:   pwsh -File scripts/stop.ps1"
