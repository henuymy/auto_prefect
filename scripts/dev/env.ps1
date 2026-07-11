$ErrorActionPreference = "Stop"

$DevScriptsDir = $PSScriptRoot
$ScriptsDir = Split-Path -Parent $DevScriptsDir
$RepoRoot = Split-Path -Parent $ScriptsDir
. (Join-Path $ScriptsDir "python_env.ps1")
$RuntimeConfigLoader = Join-Path $ScriptsDir "lib\runtime_config.ps1"
$UnifiedLocalEnvPath = Join-Path $ScriptsDir "environment.local.ps1"
$ProdLocalEnvPath = Join-Path $ScriptsDir "prefect_env_prod.local.ps1"

. $RuntimeConfigLoader
$RuntimeConfigLoaded = Import-ProjectRuntimeConfig
if ($RuntimeConfigLoaded) {
    # runtime.local.json is the preferred local configuration source.
} elseif (Test-Path -LiteralPath $UnifiedLocalEnvPath) {
    . $UnifiedLocalEnvPath
} elseif (Test-Path -LiteralPath $ProdLocalEnvPath) {
    . $ProdLocalEnvPath
} else {
    throw "缺少本机数据库配置: $ProdLocalEnvPath"
}

. (Join-Path $ScriptsDir "tools\dashboard\mysql_env.ps1")

$prodUrl = $env:AUTO_NOTIFY_PREFECT_DATABASE_URL
if (-not $prodUrl) {
    throw "AUTO_NOTIFY_PREFECT_DATABASE_URL 未配置"
}
if ($prodUrl -notmatch "/prefect(?:\?.*)?$") {
    throw "正式数据库连接串必须以 /prefect 结尾，无法自动生成 prefect_dev 地址"
}

$env:AUTO_NOTIFY_PREFECT_DEV_DATABASE_URL = $prodUrl -replace "/prefect(\?.*)?$", "/prefect_dev`$1"
if (-not $env:PREFECT_API_URL) {
    $env:PREFECT_API_URL = "http://127.0.0.1:4200/api"
}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$DevWorkPool = if ($env:PREFECT_WORK_POOL_NAME) { $env:PREFECT_WORK_POOL_NAME } else { "default-agent-pool" }
$DevBackendPort = 8000
$DevFrontendPort = 5173
$DevPythonExe = Get-ProjectPython
