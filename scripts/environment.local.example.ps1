# Copy this file to scripts/environment.local.ps1 and fill in local secrets.
$env:AUTO_NOTIFY_ENVIRONMENT = "development"
# This file is ignored by Git. Do not commit real credentials.

# Prefect PostgreSQL. This URL is passed directly to Prefect.
$env:AUTO_NOTIFY_PREFECT_DATABASE_URL = "postgresql+asyncpg://<user>:<url-encoded-password>@49.233.78.70:5432/prefect_test"

# Dashboard MySQL. Use dashboard_v2 when config/dashboard/session.json has schema_version 2.
$env:DASHBOARD_MYSQL_HOST = "49.233.78.70"
$env:DASHBOARD_MYSQL_PORT = "3306"
$env:DASHBOARD_MYSQL_DATABASE = "dashboard_v2"
$env:DASHBOARD_MYSQL_USER = "<user>"
$env:DASHBOARD_MYSQL_PASSWORD = "<password>"
$env:DASHBOARD_MYSQL_CONNECT_TIMEOUT_SECONDS = "8"
$env:DASHBOARD_MYSQL_IO_TIMEOUT_SECONDS = "60"
$env:DASHBOARD_MYSQL_POOL_RECYCLE_SECONDS = "1800"
$env:DASHBOARD_MYSQL_POOL_SIZE = "5"
$env:DASHBOARD_MYSQL_MAX_OVERFLOW = "5"
$env:DASHBOARD_MYSQL_CHARSET = "utf8mb4"
