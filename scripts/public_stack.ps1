param(
    [ValidateSet("start", "stop")]
    [string]$Action = "start",
    [string]$PrefectApiUrl = "http://127.0.0.1:4200/api",
    [string]$WorkPool = "default-agent-pool",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$FrpcExe = "",
    [string]$FrpcConfig = "",
    [switch]$UseSqliteDebug,
    [switch]$ForceRestart,
    [switch]$KillAutoNotifyPython = $true
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $FrpcExe) {
    $FrpcExe = Join-Path $RepoRoot "frp\frpc.exe"
}
if (-not $FrpcConfig) {
    $FrpcConfig = Join-Path $RepoRoot "frp\frpc.toml"
}

function Start-Window {
    param(
        [string]$Title,
        [string]$Command
    )
    $escaped = $Command.Replace('"', '\"')
    Start-Process -FilePath "pwsh" -ArgumentList @(
        "-NoExit",
        "-Command",
        "`$Host.UI.RawUI.WindowTitle='$Title'; $escaped"
    ) | Out-Null
}

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 90
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

function Get-ListeningPortOwners {
    param([int[]]$Ports)

    $owners = @()
    foreach ($port in $Ports) {
        $listeners = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
        foreach ($listener in $listeners) {
            $processName = ""
            try {
                $processName = (Get-Process -Id $listener.OwningProcess -ErrorAction Stop).ProcessName
            } catch {
                $processName = "unknown"
            }
            $owners += [pscustomobject]@{
                Port = $port
                ProcessId = $listener.OwningProcess
                ProcessName = $processName
            }
        }
    }
    return $owners
}

function Start-PublicStack {
    if (-not (Test-Path -LiteralPath $FrpcExe)) {
        throw "未找到 frpc.exe: $FrpcExe"
    }
    if (-not (Test-Path -LiteralPath $FrpcConfig)) {
        throw "未找到 frpc.toml: $FrpcConfig"
    }

    Write-Host "RepoRoot   : $RepoRoot"
    Write-Host "Prefect API: $PrefectApiUrl"
    Write-Host "Backend    : http://127.0.0.1:$BackendPort"
    Write-Host "Frontend   : http://127.0.0.1:$FrontendPort"
    Write-Host "FRP config : $FrpcConfig"
    Write-Host ""

    $requiredPorts = @(4200, $BackendPort, $FrontendPort) | Select-Object -Unique
    $busyPorts = Get-ListeningPortOwners -Ports $requiredPorts
    if ($busyPorts.Count -gt 0) {
        if ($ForceRestart) {
            Write-Host "检测到端口占用，先执行关闭再重新启动..."
            Stop-PublicStack
            Start-Sleep -Seconds 3
            $busyPorts = Get-ListeningPortOwners -Ports $requiredPorts
        }
        if ($busyPorts.Count -gt 0) {
            Write-Host "以下端口已被占用："
            $busyPorts | Format-Table -AutoSize | Out-String | Write-Host
            throw "启动已取消。请先运行 scripts\public_stack.ps1 -Action stop，或使用 -ForceRestart。"
        }
    }

    $prefectArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $PSScriptRoot "prefect_start.ps1"),
        "-Mode", "both",
        "-Detached",
        "-ApiUrl", $PrefectApiUrl,
        "-WorkPool", $WorkPool
    )
    if ($UseSqliteDebug) {
        $prefectArgs += "-UseSqliteDebug"
    }

    Write-Host "启动 Prefect Server + Worker..."
    & pwsh @prefectArgs

    if (-not (Wait-HttpOk -Url "$PrefectApiUrl/health" -TimeoutSeconds 90)) {
        throw "Prefect API 未在 90 秒内就绪: $PrefectApiUrl"
    }

    Write-Host "启动管理端后端 + React 前端..."
    $adminCommand = "& '$PSScriptRoot\admin_react_start.ps1' -Mode both -BackendPort $BackendPort -FrontendPort $FrontendPort -PrefectApiUrl '$PrefectApiUrl'"
    Start-Window -Title "Auto Notify Admin" -Command $adminCommand

    if (-not (Wait-HttpOk -Url "http://127.0.0.1:$FrontendPort" -TimeoutSeconds 60)) {
        throw "React 前端未在 60 秒内就绪: http://127.0.0.1:$FrontendPort"
    }

    Write-Host "启动 frp 公网映射..."
    $frpCommand = "& '$FrpcExe' -c '$FrpcConfig'"
    Start-Window -Title "FRP Client" -Command $frpCommand

    Write-Host ""
    Write-Host "一键启动完成。"
    Write-Host "本机前端: http://127.0.0.1:$FrontendPort"
    Write-Host "本机后端: http://127.0.0.1:$BackendPort/api/health"
    Write-Host "Prefect UI: http://127.0.0.1:4200"
    Write-Host "公网访问请使用 frps 服务器 IP + frpc.toml 中的 remotePort。"
}

function Stop-PublicStack {
    $ErrorActionPreference = "Continue"
    $ports = @(4200, $BackendPort, $FrontendPort) | Select-Object -Unique

    Write-Host "RepoRoot : $RepoRoot"
    Write-Host "Ports    : $($ports -join ', ')"
    Write-Host "FRP exe  : $FrpcExe"
    Write-Host ""

    Write-Host "停止 Prefect..."
    & (Join-Path $PSScriptRoot "prefect_stop.ps1") -Ports @($ports | Where-Object { $_ -eq 4200 }) -KillAutoNotifyPython:$KillAutoNotifyPython

    $killedByPort = @()
    foreach ($port in ($ports | Where-Object { $_ -ne 4200 })) {
        $listeners = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
        foreach ($listener in $listeners) {
            if ($listener.OwningProcess -and -not ($killedByPort -contains $listener.OwningProcess)) {
                Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
                $killedByPort += $listener.OwningProcess
            }
        }
    }

    $killedFrp = @()
    $frpcPath = $null
    if (Test-Path -LiteralPath $FrpcExe) {
        $frpcPath = (Resolve-Path -LiteralPath $FrpcExe).Path
    }
    $frpProcesses = Get-Process -Name "frpc" -ErrorAction SilentlyContinue
    foreach ($proc in $frpProcesses) {
        if (-not $frpcPath -or ($proc.Path -and $proc.Path -ieq $frpcPath)) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            $killedFrp += $proc.Id
        }
    }

    $killedShell = @()
    $shells = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -in @("pwsh.exe", "powershell.exe", "cmd.exe") -and
            $_.CommandLine -and
            (
                $_.CommandLine -like "*admin_react_start.ps1*" -or
                $_.CommandLine -like "*npm run dev*" -or
                $_.CommandLine -like "*uvicorn backend.app:app*" -or
                $_.CommandLine -like "*frpc.exe*" -or
                $_.CommandLine -like "*Auto Notify Admin*" -or
                $_.CommandLine -like "*FRP Client*"
            )
        }
    foreach ($proc in $shells) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        $killedShell += $proc.ProcessId
    }

    Write-Host "Stopped by port: $($killedByPort -join ',')"
    Write-Host "Stopped frpc   : $($killedFrp -join ',')"
    Write-Host "Stopped shell  : $($killedShell -join ',')"
    Write-Host "Done."
}

switch ($Action) {
    "start" { Start-PublicStack }
    "stop" { Stop-PublicStack }
}
