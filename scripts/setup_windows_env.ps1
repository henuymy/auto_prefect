param(
    [string]$EnvName = "auto-notify",
    [string]$PythonVersion = "3.11",
    [string]$PipIndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple",
    [string]$PipTrustedHost = "pypi.tuna.tsinghua.edu.cn",
    [switch]$SkipCondaMirror,
    [switch]$SkipCondaCreate,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
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

function Require-Command {
    param(
        [string]$Name,
        [string]$HelpMessage
    )
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw $HelpMessage
    }
    return $cmd
}

function Test-CondaEnvExists {
    param([string]$Name)
    $envList = & conda env list 2>$null
    if (-not $envList) {
        return $false
    }
    return [bool]($envList | Select-String -Pattern "^\s*$([regex]::Escape($Name))\s")
}

function Ensure-CondaMirror {
    if ($SkipCondaMirror) {
        Write-Host "已跳过 conda 镜像配置。"
        return
    }

    $mirrorCommands = @(
        "config --remove-key channels",
        "config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main",
        "config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/free",
        "config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/r",
        "config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge",
        "config --set show_channel_urls yes"
    )

    foreach ($cmd in $mirrorCommands) {
        try {
            & conda ($cmd -split " ")
        } catch {
            if ($cmd -ne "config --remove-key channels") {
                throw
            }
        }
    }

    Write-Host "已写入 conda 清华镜像。"
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

Require-Command -Name "conda" -HelpMessage "未找到 conda，请先安装 Anaconda 或 Miniconda。"

Write-Step "配置 Conda 国内镜像"
Ensure-CondaMirror

Write-Step "准备 Conda 环境"
if (-not $SkipCondaCreate) {
    if (Test-CondaEnvExists -Name $EnvName) {
        Write-Host "Conda 环境已存在：$EnvName"
    } else {
        & conda create -n $EnvName "python=$PythonVersion" -y
    }
} else {
    Write-Host "已跳过 conda create。"
}

$CondaPython = Join-Path $env:USERPROFILE ".conda\envs\$EnvName\python.exe"
if (-not (Test-Path -LiteralPath $CondaPython)) {
    throw "未找到环境 Python：$CondaPython。请确认 Conda 环境 '$EnvName' 已正确创建。"
}

Write-Step "安装 Python 依赖"
& $CondaPython -m pip install --upgrade pip setuptools wheel -i $PipIndexUrl --trusted-host $PipTrustedHost
& $CondaPython -m pip install -r (Join-Path $RepoRoot "requirements.txt") -i $PipIndexUrl --trusted-host $PipTrustedHost

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

Write-Step "写入 autologin Python 路径"
$autologinPath = Join-Path $RepoRoot "config\modules\autologin.json"
if (Test-Path -LiteralPath $autologinPath) {
    $json = Get-Content -Raw $autologinPath | ConvertFrom-Json
    $json.login_command = "`"$CondaPython`" -c `"from services.login_service import run_login; run_login('config/modules/login_config.json')`""
    $json | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $autologinPath -Encoding UTF8
    Write-Host "已更新 autologin.json 中的 login_command。"
}

if (-not $SkipSmokeTest) {
    Write-Step "执行基础自检"
    & $CondaPython -c "import prefect, requests, selenium, streamlit, asyncpg; import services.login_service; import services.session_manager; print('smoke_ok')"
}

Write-Step "完成"
Write-Host "环境名称      : $EnvName"
Write-Host "Python 路径    : $CondaPython"
Write-Host "项目目录      : $RepoRoot"
Write-Host "PIP 镜像源    : $PipIndexUrl"
Write-Host "Conda 镜像    : $(if ($SkipCondaMirror) { '保持现状' } else { '清华镜像' })"
Write-Host ""
Write-Host "后续常用命令：" -ForegroundColor Green
Write-Host "1. conda activate $EnvName"
Write-Host "2. 本地调试:  . .\scripts\prefect_env_debug.ps1"
Write-Host "3. 正式调度:  . .\scripts\prefect_env_prod.ps1"
Write-Host "4. 启动 UI:   streamlit run admin/app.py"
Write-Host "5. 单条运行:  python -c `"from flows.notify_single_flow import auto_notify_flow; print(auto_notify_flow('config/tasks/日通报.json'))`""
