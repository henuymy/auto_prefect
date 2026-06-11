$DashboardMySqlLocalEnvPath = Join-Path $PSScriptRoot "mysql_env.local.ps1"

if (Test-Path -LiteralPath $DashboardMySqlLocalEnvPath) {
    . $DashboardMySqlLocalEnvPath
}
