function Get-ManagedProcessRegistryRoot {
    $runtimeRoot = $env:AUTO_NOTIFY_RUNTIME_ROOT
    if ([string]::IsNullOrWhiteSpace($runtimeRoot)) {
        $runtimeRoot = "C:\AutoNotifyRuntime"
    }
    return (Join-Path $runtimeRoot "processes")
}

function Get-StartupMutexName {
    $runtimeRoot = [System.IO.Path]::GetFullPath(
        $(if ($env:AUTO_NOTIFY_RUNTIME_ROOT) {
            $env:AUTO_NOTIFY_RUNTIME_ROOT
        } else {
            "C:\AutoNotifyRuntime"
        })
    ).ToUpperInvariant()
    $bytes = [Text.Encoding]::UTF8.GetBytes($runtimeRoot)
    $hash = [Convert]::ToHexString(
        [Security.Cryptography.SHA256]::HashData($bytes)
    ).Substring(0, 24)
    return "Global\AutoNotifyStartup-$hash"
}

function Enter-StartupClaim {
    $mutex = [Threading.Mutex]::new($false, (Get-StartupMutexName))
    try {
        try {
            $acquired = $mutex.WaitOne(0)
        } catch [Threading.AbandonedMutexException] {
            $acquired = $true
        }
        if (-not $acquired) {
            throw "Another scripts\run.ps1 startup is already in progress"
        }
        return [pscustomobject]@{ Mutex = $mutex; Acquired = $true }
    } catch {
        $mutex.Dispose()
        throw
    }
}

function Exit-StartupClaim {
    param([Parameter(Mandatory = $true)][object]$Claim)
    if ($Claim.Acquired) {
        $Claim.Mutex.ReleaseMutex()
        $Claim.Acquired = $false
    }
    $Claim.Mutex.Dispose()
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

function Test-ManagedProcessIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Identity
    )

    return (Test-ManagedProcessRecord -Record $Identity)
}

function Get-ManagedProcessTreeSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [object]$RootRecord
    )

    if (-not (Test-ManagedProcessIdentity -Identity $RootRecord)) {
        throw "Managed root process identity changed before tree capture"
    }

    $rootProcessId = [int]$RootRecord.pid
    $ownedProcessIds = @{}
    $ownedProcessIds[$rootProcessId] = $true

    if ($env:OS -eq "Windows_NT") {
        $systemProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        do {
            $added = $false
            foreach ($systemProcess in $systemProcesses) {
                $processId = [int]$systemProcess.ProcessId
                $parentProcessId = [int]$systemProcess.ParentProcessId
                if (-not $ownedProcessIds.ContainsKey($processId) -and
                    $ownedProcessIds.ContainsKey($parentProcessId)) {
                    $ownedProcessIds[$processId] = $true
                    $added = $true
                }
            }
        } while ($added)
    }

    $snapshot = @()
    foreach ($processId in @($ownedProcessIds.Keys)) {
        try {
            $process = Get-Process -Id $processId -ErrorAction Stop
            $process.Refresh()
            $snapshot += [pscustomobject]@{
                pid = $process.Id
                process_started_at = $process.StartTime.ToString("o")
            }
        } catch {
            if ($processId -eq $rootProcessId) {
                throw "Managed root process exited during tree capture: PID $rootProcessId"
            }
        }
    }

    $capturedRoot = $snapshot | Where-Object { [int]$_.pid -eq $rootProcessId } | Select-Object -First 1
    if ($null -eq $capturedRoot -or -not (Test-ManagedProcessIdentity -Identity $capturedRoot) -or
        ([DateTimeOffset]$capturedRoot.process_started_at).UtcTicks -ne
        ([DateTimeOffset]$RootRecord.process_started_at).UtcTicks) {
        throw "Managed root process identity changed during tree capture: PID $rootProcessId"
    }

    return @($snapshot)
}

function Invoke-ManagedTaskkill {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ProcessId
    )

    if ($env:OS -eq "Windows_NT") {
        & taskkill.exe /PID $ProcessId /T /F *> $null
        return $LASTEXITCODE -eq 0
    }

    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Wait-ManagedProcessTreeExit {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Snapshot,
        [int]$TimeoutSeconds = 15
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $remaining = @($Snapshot | Where-Object {
            Test-ManagedProcessIdentity -Identity $_
        })
        if ($remaining.Count -eq 0) {
            return @()
        }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $deadline)

    return @($remaining)
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

    $processId = [int]$record.pid
    $snapshot = @(Get-ManagedProcessTreeSnapshot -RootRecord $record)
    if ($snapshot.Count -eq 0) {
        throw "Managed process tree capture was empty: $Name"
    }
    if (-not (Invoke-ManagedTaskkill -ProcessId $processId)) {
        throw "taskkill failed for managed component: $Name (PID $processId)"
    }

    $remaining = @(Wait-ManagedProcessTreeExit -Snapshot $snapshot)
    if ($remaining.Count -gt 0) {
        $remainingIds = @($remaining | ForEach-Object { [int]$_.pid }) -join ","
        throw "Managed process tree did not fully exit: $Name; remaining PIDs: $remainingIds"
    }

    Remove-Item -LiteralPath $path -Force -ErrorAction Stop
    Write-Host "[STOPPED] $Name (PID $processId)"
    return $true
}
