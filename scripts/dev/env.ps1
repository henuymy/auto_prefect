$ErrorActionPreference = "Stop"

$DevScriptsDir = $PSScriptRoot
$ScriptsDir = Split-Path -Parent $DevScriptsDir
$RepoRoot = Split-Path -Parent $ScriptsDir
. (Join-Path $ScriptsDir "python_env.ps1")
$ProdLocalEnvPath = Join-Path $ScriptsDir "prefect_env_prod.local.ps1"

if (-not (Test-Path -LiteralPath $ProdLocalEnvPath)) {
    throw "缺少本机数据库配置: $ProdLocalEnvPath"
}

. $ProdLocalEnvPath
. (Join-Path $ScriptsDir "dashboard\mysql_env.ps1")

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

$DevWorkPool = "default-agent-pool"
$DevBackendPort = 8000
$DevFrontendPort = 5173
$DevPythonExe = Get-ProjectPython
