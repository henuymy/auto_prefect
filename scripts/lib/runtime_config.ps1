function Get-RuntimeConfigValue {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Config,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $value = $Config
    foreach ($segment in $Path.Split('.')) {
        $property = $value.PSObject.Properties[$segment]
        if ($null -eq $property) {
            throw "运行配置缺少必填字段: $Path"
        }
        $value = $property.Value
    }

    if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value)) {
        throw "运行配置必填字段不能为空: $Path"
    }

    return $value
}

function Import-RuntimeConfig {
    param(
        [string]$ConfigPath = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "config\runtime.local.json")
    )

    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw "运行配置不存在: $ConfigPath。请从 config\\runtime.local.example.json 创建本机配置。"
    }

    try {
        $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "无法解析运行配置 JSON: $ConfigPath。$($_.Exception.Message)"
    }

    $env:AUTO_NOTIFY_PREFECT_DATABASE_URL = Get-RuntimeConfigValue -Config $config -Path "prefect.postgres.url"
    $env:PREFECT_API_URL = Get-RuntimeConfigValue -Config $config -Path "prefect.api_url"
    $env:DASHBOARD_MYSQL_HOST = Get-RuntimeConfigValue -Config $config -Path "dashboard.mysql.host"
    $env:DASHBOARD_MYSQL_PORT = Get-RuntimeConfigValue -Config $config -Path "dashboard.mysql.port"
    $env:DASHBOARD_MYSQL_DATABASE = Get-RuntimeConfigValue -Config $config -Path "dashboard.mysql.database"
    $env:DASHBOARD_MYSQL_USER = Get-RuntimeConfigValue -Config $config -Path "dashboard.mysql.user"
    $env:DASHBOARD_MYSQL_PASSWORD = Get-RuntimeConfigValue -Config $config -Path "dashboard.mysql.password"
    $env:PREFECT_WORK_POOL_NAME = Get-RuntimeConfigValue -Config $config -Path "runtime.work_pool"

    return $config
}

function Import-ProjectRuntimeConfig {
    param(
        [string]$ConfigPath = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "config\runtime.local.json")
    )

    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        return $false
    }

    Import-RuntimeConfig -ConfigPath $ConfigPath | Out-Null
    return $true
}
