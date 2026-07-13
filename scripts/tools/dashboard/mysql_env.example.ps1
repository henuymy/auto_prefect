# Copy this file to scripts/tools/dashboard/mysql_env.local.ps1 and fill in local values.
# The active runtime uses Dashboard V2 only. To use another ignored local
# profile, set DASHBOARD_MYSQL_ENV_FILE before loading mysql_env.ps1.
# The *.local.ps1 file is ignored by Git.

$env:DASHBOARD_MYSQL_HOST = "127.0.0.1"
$env:DASHBOARD_MYSQL_PORT = "3306"
$env:DASHBOARD_MYSQL_DATABASE = "dashboard_v2"
$env:DASHBOARD_MYSQL_USER = "dashboard_app"
$env:DASHBOARD_MYSQL_PASSWORD = ""

$env:DASHBOARD_MYSQL_CONNECT_TIMEOUT_SECONDS = "5"
$env:DASHBOARD_MYSQL_IO_TIMEOUT_SECONDS = "60"
$env:DASHBOARD_MYSQL_POOL_RECYCLE_SECONDS = "1800"
$env:DASHBOARD_MYSQL_POOL_SIZE = "5"
$env:DASHBOARD_MYSQL_MAX_OVERFLOW = "5"
$env:DASHBOARD_MYSQL_CHARSET = "utf8mb4"
