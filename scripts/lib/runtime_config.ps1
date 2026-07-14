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

function Get-RuntimeConfigPositiveInt {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Config,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $value = Get-RuntimeConfigValue -Config $Config -Path $Path
    $numericTypeCodes = @(
        [System.TypeCode]::Byte,
        [System.TypeCode]::SByte,
        [System.TypeCode]::UInt16,
        [System.TypeCode]::UInt32,
        [System.TypeCode]::UInt64,
        [System.TypeCode]::Int16,
        [System.TypeCode]::Int32,
        [System.TypeCode]::Int64,
        [System.TypeCode]::Decimal,
        [System.TypeCode]::Double,
        [System.TypeCode]::Single
    )
    if ([System.Type]::GetTypeCode($value.GetType()) -notin $numericTypeCodes) {
        throw "运行配置必须为正整数: $Path"
    }

    $numericValue = [double]$value
    if ([double]::IsNaN($numericValue) -or
        [double]::IsInfinity($numericValue) -or
        $numericValue -le 0 -or
        $numericValue -ne [math]::Truncate($numericValue) -or
        $numericValue -gt [int]::MaxValue) {
        throw "运行配置必须为正整数: $Path"
    }

    return [int]$numericValue
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
    $env:AUTO_NOTIFY_RUNTIME_ROOT = Get-RuntimeConfigValue -Config $config -Path "runtime.root"
    $env:PREFECT_SESSION_POOL_NAME = Get-RuntimeConfigValue -Config $config -Path "runtime.work_pools.session.name"
    $env:PREFECT_SESSION_POOL_LIMIT = Get-RuntimeConfigPositiveInt -Config $config -Path "runtime.work_pools.session.limit"
    $env:PREFECT_DASHBOARD_POOL_NAME = Get-RuntimeConfigValue -Config $config -Path "runtime.work_pools.dashboard.name"
    $env:PREFECT_DASHBOARD_POOL_LIMIT = Get-RuntimeConfigPositiveInt -Config $config -Path "runtime.work_pools.dashboard.limit"
    $env:PREFECT_NOTIFY_POOL_NAME = Get-RuntimeConfigValue -Config $config -Path "runtime.work_pools.notify.name"
    $env:PREFECT_NOTIFY_POOL_LIMIT = Get-RuntimeConfigPositiveInt -Config $config -Path "runtime.work_pools.notify.limit"
    $env:AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS = Get-RuntimeConfigPositiveInt -Config $config -Path "runtime.scheduled_notify_grace_seconds"

    $poolNames = @(
        $env:PREFECT_SESSION_POOL_NAME,
        $env:PREFECT_DASHBOARD_POOL_NAME,
        $env:PREFECT_NOTIFY_POOL_NAME
    )
    if (($poolNames | Select-Object -Unique).Count -ne 3) {
        throw "三个 Prefect Work Pool 名称必须互不相同"
    }

    $expectedPools = @{
        session = "windows-session-pool"
        dashboard = "windows-dashboard-pool"
        notify = "windows-notify-pool"
    }
    if ($env:PREFECT_SESSION_POOL_NAME -ne $expectedPools.session -or
        $env:PREFECT_DASHBOARD_POOL_NAME -ne $expectedPools.dashboard -or
        $env:PREFECT_NOTIFY_POOL_NAME -ne $expectedPools.notify) {
        throw "runtime.local.json 的 Work Pool 名称必须与 prefect.yaml 的三 Pool 拓扑一致"
    }

    $env:PREFECT_WORK_POOL_NAME = $env:PREFECT_NOTIFY_POOL_NAME

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
