function Get-ManagedProcessRegistryRoot {
    $runtimeRoot = $env:AUTO_NOTIFY_RUNTIME_ROOT
    if ([string]::IsNullOrWhiteSpace($runtimeRoot)) {
        $runtimeRoot = "C:\AutoNotifyRuntime"
    }
    return (Join-Path $runtimeRoot "processes")
}

function Get-ManagedProcessRecordPath {
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[A-Za-z0-9_-]+$')]
        [string]$Name
    )

    return (Join-Path (Get-ManagedProcessRegistryRoot) "$Name.json")
}

function Get-ManagedProcessRecord {
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[A-Za-z0-9_-]+$')]
        [string]$Name
    )

    $path = Get-ManagedProcessRecordPath -Name $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return $null
    }
    try {
        return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return $null
    }
}

function Test-ManagedProcessRecord {
    param(
        [Parameter(Mandatory = $false)]
        [object]$Record
    )

    if ($null -eq $Record -or $null -eq $Record.pid -or
        [string]::IsNullOrWhiteSpace([string]$Record.process_started_at)) {
        return $false
    }
    try {
        $pidValue = [int]$Record.pid
        if ($pidValue -le 0) {
            return $false
        }
        $process = Get-Process -Id $pidValue -ErrorAction Stop
        $process.Refresh()
        $recordedStartTime = [DateTimeOffset]$Record.process_started_at
        $actualStartTime = [DateTimeOffset]$process.StartTime
        return $actualStartTime.UtcTicks -eq $recordedStartTime.UtcTicks
    } catch {
        return $false
    }
}

function Assert-ManagedProcessAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[A-Za-z0-9_-]+$')]
        [string]$Name
    )

    $record = Get-ManagedProcessRecord -Name $Name
    if (Test-ManagedProcessRecord -Record $record) {
        throw "Managed component is already running: $Name (PID $($record.pid))"
    }
    if ($null -ne $record) {
        Remove-Item -LiteralPath (Get-ManagedProcessRecordPath -Name $Name) -Force -ErrorAction SilentlyContinue
    }
}

function Register-ManagedProcess {
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[A-Za-z0-9_-]+$')]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)]
        [string]$Command
    )

    $existing = Get-ManagedProcessRecord -Name $Name
    if (Test-ManagedProcessRecord -Record $existing) {
        throw "Managed component is already running: $Name (PID $($existing.pid))"
    }

    $Process.Refresh()
    if ($Process.HasExited) {
        throw "Cannot register exited managed component: $Name"
    }

    $directory = Get-ManagedProcessRegistryRoot
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $path = Get-ManagedProcessRecordPath -Name $Name
    $temporaryPath = Join-Path $directory (".{0}.{1}.tmp" -f $Name, [guid]::NewGuid().ToString("N"))
    $record = [ordered]@{
        name = $Name
        pid = $Process.Id
        process_started_at = $Process.StartTime.ToString("o")
        registered_at = [DateTimeOffset]::Now.ToString("o")
        command = $Command
    }
    try {
        $record | ConvertTo-Json | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
        Move-Item -LiteralPath $temporaryPath -Destination $path -Force
    } finally {
        Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
    }
    return [pscustomobject]$record
}

function Stop-ManagedProcessTree {
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[A-Za-z0-9_-]+$')]
        [string]$Name
    )

    $path = Get-ManagedProcessRecordPath -Name $Name
    $record = Get-ManagedProcessRecord -Name $Name
    if ($null -eq $record) {
        Write-Host "[ABSENT] $Name"
        return $false
    }
    if (-not (Test-ManagedProcessRecord -Record $record)) {
        Write-Host "[STALE]  $Name registry record did not match a live process"
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        return $false
    }

    $pidValue = [int]$record.pid
    if ($env:OS -eq "Windows_NT") {
        & taskkill.exe /PID $pidValue /T /F *> $null
    } else {
        Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
    }

    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline -and (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) {
        Start-Sleep -Milliseconds 200
    }
    if (Get-Process -Id $pidValue -ErrorAction SilentlyContinue) {
        Write-Warning "Managed component did not exit: $Name (PID $pidValue)"
        return $false
    }

    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    Write-Host "[STOPPED] $Name (PID $pidValue)"
    return $true
}
