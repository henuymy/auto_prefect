$RepoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$UnifiedLocalEnvPath = Join-Path $RepoRoot "scripts\environment.local.ps1"
. (Join-Path $RepoRoot "scripts\lib\runtime_config.ps1")
if (Import-ProjectRuntimeConfig) {
    return
} elseif (Test-Path -LiteralPath $UnifiedLocalEnvPath) {
    . $UnifiedLocalEnvPath
    return
}

$DashboardMySqlLocalEnvPath = if ($env:DASHBOARD_MYSQL_ENV_FILE) {
    $configuredPath = $env:DASHBOARD_MYSQL_ENV_FILE.Trim()
    if ([System.IO.Path]::IsPathRooted($configuredPath)) {
        $configuredPath
    } else {
        Join-Path $RepoRoot $configuredPath
    }
} else {
    Join-Path $PSScriptRoot "mysql_env.v2.local.ps1"
}

if (Test-Path -LiteralPath $DashboardMySqlLocalEnvPath) {
    . $DashboardMySqlLocalEnvPath
} else {
    throw "驾驶舱 MySQL profile 不存在: $DashboardMySqlLocalEnvPath"
}
