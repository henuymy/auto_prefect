$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$DashboardMySqlLocalEnvPath = if ($env:DASHBOARD_MYSQL_ENV_FILE) {
    $configuredPath = $env:DASHBOARD_MYSQL_ENV_FILE.Trim()
    if ([System.IO.Path]::IsPathRooted($configuredPath)) {
        $configuredPath
    } else {
        Join-Path $RepoRoot $configuredPath
    }
} else {
    Join-Path $PSScriptRoot "mysql_env.local.ps1"
}

if (Test-Path -LiteralPath $DashboardMySqlLocalEnvPath) {
    . $DashboardMySqlLocalEnvPath
} elseif ($env:DASHBOARD_MYSQL_ENV_FILE) {
    throw "DASHBOARD_MYSQL_ENV_FILE 指向的文件不存在: $DashboardMySqlLocalEnvPath"
}
