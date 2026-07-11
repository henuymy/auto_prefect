param(
    [string]$PipIndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple",
    [string]$PipTrustedHost = "pypi.tuna.tsinghua.edu.cn",
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "python_env.ps1")
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
foreach ($dir in $RuntimeDirs) {
    New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot $dir) | Out-Null
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
Write-Host "PIP 镜像源    : $PipIndexUrl"
Write-Host ""
Write-Host "后续常用命令：" -ForegroundColor Green
Write-Host "1. 本地调试:  . .\scripts\prefect_env_debug.ps1"
Write-Host "2. 正式调度:  . .\scripts\prefect_env_prod.ps1"
Write-Host "3. 启动系统: pwsh -File scripts/start_web.ps1 -Mode both"
Write-Host "4. 单条运行:  python -c `"from flows.notify_single_flow import auto_notify_flow; print(auto_notify_flow('config/tasks/日通报.json'))`""
