$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
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
    $sessionConfigPath = Join-Path $RepoRoot "config\dashboard\session.json"
    if (-not (Test-Path -LiteralPath $sessionConfigPath)) {
        throw "驾驶舱配置不存在，无法自动选择 MySQL profile: $sessionConfigPath"
    }
    try {
        $schemaVersion = [int]((Get-Content -LiteralPath $sessionConfigPath -Raw | ConvertFrom-Json).schema_version)
    } catch {
        throw "读取驾驶舱 schema_version 失败: $sessionConfigPath"
    }
    $profileName = switch ($schemaVersion) {
        1 { "mysql_env.v1.local.ps1" }
        2 { "mysql_env.v2.local.ps1" }
        default { throw "驾驶舱 schema_version 只支持 1/2，当前值: $schemaVersion" }
    }
    Join-Path $PSScriptRoot $profileName
}

if (Test-Path -LiteralPath $DashboardMySqlLocalEnvPath) {
    . $DashboardMySqlLocalEnvPath
} else {
    throw "驾驶舱 MySQL profile 不存在: $DashboardMySqlLocalEnvPath"
}
