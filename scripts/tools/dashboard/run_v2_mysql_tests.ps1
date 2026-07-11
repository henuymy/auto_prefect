param(
    [string]$TestDatabase = "dashboard_v2_ci"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
. (Join-Path $ScriptDir "mysql_env.ps1")

if (-not $env:DASHBOARD_MYSQL_PASSWORD) {
    throw "DASHBOARD_MYSQL_PASSWORD 未配置"
}
if ($TestDatabase -notmatch "(^test|test$|_test$|_ci$)") {
    throw "测试库名必须以 test/ci 规则命名，拒绝执行: $TestDatabase"
}

$previousDatabase = $env:DASHBOARD_MYSQL_DATABASE
$previousTestUrl = $env:DASHBOARD_TEST_MYSQL_URL
try {
    $env:DASHBOARD_MYSQL_DATABASE = $TestDatabase
    $env:DASHBOARD_TEST_MYSQL_URL = & python -c @"
from infrastructure.dashboard_mysql import DashboardMySQLSettings
print(DashboardMySQLSettings.from_env().sqlalchemy_url().render_as_string(hide_password=False))
"@
    if ($LASTEXITCODE -ne 0 -or -not $env:DASHBOARD_TEST_MYSQL_URL) {
        throw "无法生成测试数据库连接串"
    }
    Push-Location $RepoRoot
    try {
        python -m pytest tests/test_dashboard_v2_mysql_integration.py -v
        if ($LASTEXITCODE -ne 0) {
            throw "Dashboard V2 MySQL 集成测试失败"
        }
    } finally {
        Pop-Location
    }
} finally {
    $env:DASHBOARD_MYSQL_DATABASE = $previousDatabase
    if ($null -eq $previousTestUrl) {
        Remove-Item Env:DASHBOARD_TEST_MYSQL_URL -ErrorAction SilentlyContinue
    } else {
        $env:DASHBOARD_TEST_MYSQL_URL = $previousTestUrl
    }
}
