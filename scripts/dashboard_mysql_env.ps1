$DashboardMySqlLocalEnvPath = Join-Path $PSScriptRoot "dashboard_mysql_env.local.ps1"

if (Test-Path -LiteralPath $DashboardMySqlLocalEnvPath) {
    . $DashboardMySqlLocalEnvPath
}
