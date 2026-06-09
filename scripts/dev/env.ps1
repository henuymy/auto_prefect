$ErrorActionPreference = "Stop"

$DevScriptsDir = $PSScriptRoot
$ScriptsDir = Split-Path -Parent $DevScriptsDir
$RepoRoot = Split-Path -Parent $ScriptsDir
$ProdLocalEnvPath = Join-Path $ScriptsDir "prefect_env_prod.local.ps1"

if (-not (Test-Path -LiteralPath $ProdLocalEnvPath)) {
    throw "缺少本机数据库配置: $ProdLocalEnvPath"
}

. $ProdLocalEnvPath

$prodUrl = $env:AUTO_NOTIFY_PREFECT_DATABASE_URL
if (-not $prodUrl) {
    throw "AUTO_NOTIFY_PREFECT_DATABASE_URL 未配置"
}
if ($prodUrl -notmatch "/prefect(?:\?.*)?$") {
    throw "正式数据库连接串必须以 /prefect 结尾，无法自动生成 prefect_dev 地址"
}

$env:AUTO_NOTIFY_PREFECT_DEV_DATABASE_URL = $prodUrl -replace "/prefect(\?.*)?$", "/prefect_dev`$1"
$env:PREFECT_API_URL = "http://127.0.0.1:4200/api"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONNOUSERSITE = "1"

$DevWorkPool = "dev-agent-pool"
$DevBackendPort = 8000
$DevFrontendPort = 5173
$DevPythonExe = Join-Path $env:USERPROFILE ".conda\envs\auto-notify\python.exe"

$condaCmd = Get-Command conda -ErrorAction SilentlyContinue
if ($condaCmd) {
    $envInfo = (& conda env list 2>$null) |
        Select-String -Pattern "^\s*auto-notify\s+(.+)$" |
        Select-Object -First 1
    if ($envInfo) {
        $candidatePython = Join-Path $envInfo.Matches[0].Groups[1].Value.Trim() "python.exe"
        if (Test-Path -LiteralPath $candidatePython) {
            $DevPythonExe = $candidatePython
        }
    }
}

if (-not (Test-Path -LiteralPath $DevPythonExe)) {
    throw "未找到 auto-notify Python: $DevPythonExe"
}
