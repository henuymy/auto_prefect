param(
    [string]$TaskDir = "config/tasks",
    [string]$PythonExe = "",
    [int]$TimeoutSeconds = 1800,
    [switch]$KeepProfile,
    [switch]$NoRun
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$TaskRoot = if ([System.IO.Path]::IsPathRooted($TaskDir)) {
    [System.IO.Path]::GetFullPath($TaskDir)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $TaskDir))
}

function Resolve-PythonExe {
    param([string]$Candidate)

    if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
        return [System.IO.Path]::GetFullPath($Candidate)
    }

    $userConda = Join-Path $env:USERPROFILE ".conda\envs\auto-notify\python.exe"
    if (Test-Path -LiteralPath $userConda) {
        return [System.IO.Path]::GetFullPath($userConda)
    }

    if ($env:CONDA_PREFIX) {
        $condaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $condaPython) {
            return [System.IO.Path]::GetFullPath($condaPython)
        }
    }

    $condaCmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($condaCmd) {
        $envInfo = (& conda env list 2>$null) |
            Select-String -Pattern "^\s*auto-notify\s+(.+)$" |
            Select-Object -First 1
        if ($envInfo) {
            $condaPython = Join-Path $envInfo.Matches[0].Groups[1].Value.Trim() "python.exe"
            if (Test-Path -LiteralPath $condaPython) {
                return [System.IO.Path]::GetFullPath($condaPython)
            }
        }
    }

    return "python"
}

function Remove-PathIfExists {
    param(
        [string]$Path,
        [switch]$Recurse
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Host "Skip missing: $Path"
        return
    }

    if ($Recurse) {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
    } else {
        Remove-Item -LiteralPath $Path -Force -ErrorAction Stop
    }
    Write-Host "Removed: $Path"
}

function Convert-ToRepoRelativePath {
    param([string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetFullPath($RepoRoot)
    if (-not $root.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $root += [System.IO.Path]::DirectorySeparatorChar
    }

    if ($fullPath.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $fullPath.Substring($root.Length).Replace("\", "/")
    }
    return $fullPath.Replace("\", "/")
}

$PythonExe = Resolve-PythonExe -Candidate $PythonExe
$CookieDumpPath = Join-Path $env:AUTO_NOTIFY_RUNTIME_ROOT "session\cookie_dump.json"
$LoginLockPath = Join-Path $env:AUTO_NOTIFY_RUNTIME_ROOT "session\locks\login.lock"
$BrowserSessionPath = Join-Path $env:AUTO_NOTIFY_RUNTIME_ROOT "session\browser-session.json"
$BrowserProfilePath = Join-Path $env:AUTO_NOTIFY_RUNTIME_ROOT "session\browser-profile"

Write-Host "RepoRoot      : $RepoRoot"
Write-Host "TaskDir       : $TaskRoot"
Write-Host "PythonExe     : $PythonExe"
Write-Host "TimeoutSeconds: $TimeoutSeconds"
Write-Host "KeepProfile   : $KeepProfile"
Write-Host "NoRun         : $NoRun"
Write-Host ""

if (-not (Test-Path -LiteralPath $TaskRoot)) {
    throw "TaskDir does not exist: $TaskRoot"
}

Write-Host "Closing recorded auto-login Edge session..."
Push-Location $RepoRoot
try {
    & $PythonExe -c "from services.browser_session import close_browser_session; print(close_browser_session())"
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Cleaning login state..."
Remove-PathIfExists -Path $CookieDumpPath
Remove-PathIfExists -Path $LoginLockPath
Remove-PathIfExists -Path $BrowserSessionPath
if (-not $KeepProfile) {
    Remove-PathIfExists -Path $BrowserProfilePath -Recurse
} else {
    Write-Host "Keep profile: $BrowserProfilePath"
}

$TaskFiles = Get-ChildItem -LiteralPath $TaskRoot -Filter "*.json" -File |
    Sort-Object Name

if (-not $TaskFiles) {
    throw "No task JSON files found in: $TaskRoot"
}

Write-Host ""
Write-Host "Found task files:"
foreach ($taskFile in $TaskFiles) {
    Write-Host " - $(Convert-ToRepoRelativePath -Path $taskFile.FullName)"
}

if ($NoRun) {
    Write-Host ""
    Write-Host "NoRun is set. Clean login simulation state is ready."
    exit 0
}

Write-Host ""
Write-Host "Starting concurrent task jobs..."
$Jobs = @()
foreach ($taskFile in $TaskFiles) {
    $relativeTaskPath = Convert-ToRepoRelativePath -Path $taskFile.FullName
    $job = Start-Job -Name $taskFile.BaseName -ArgumentList $RepoRoot, $PythonExe, $relativeTaskPath -ScriptBlock {
        param($JobRepoRoot, $JobPythonExe, $JobTaskPath)

        Set-Location $JobRepoRoot
        $env:PYTHONUTF8 = "1"
        $env:PYTHONIOENCODING = "utf-8"

        $code = "from flows.notify_single_flow import auto_notify_flow; auto_notify_flow(r'$JobTaskPath')"
        & $JobPythonExe -c $code
    }
    $Jobs += [PSCustomObject]@{
        Task = $relativeTaskPath
        Job = $job
    }
    Write-Host (" - Started {0}: JobId={1}" -f $relativeTaskPath, $job.Id)
}

Write-Host ""
Write-Host "Waiting for jobs..."
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    $running = @($Jobs | Where-Object { $_.Job.State -in @("NotStarted", "Running") })
    if ($running.Count -eq 0) {
        break
    }
    Start-Sleep -Seconds 5
}

$timedOut = @($Jobs | Where-Object { $_.Job.State -in @("NotStarted", "Running") })
if ($timedOut.Count -gt 0) {
    Write-Warning "Timeout reached. Stopping unfinished jobs..."
    foreach ($item in $timedOut) {
        Stop-Job -Job $item.Job -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "Job results:"
$failed = @()
foreach ($item in $Jobs) {
    $job = $item.Job
    Write-Host ""
    Write-Host ("===== {0} | JobId={1} | State={2} =====" -f $item.Task, $job.Id, $job.State)
    Receive-Job -Job $job -Keep
    if ($job.State -ne "Completed") {
        $failed += $item
    }
}

Write-Host ""
Write-Host "Final state:"
Write-Host (" - Cookie dump exists: {0}" -f (Test-Path -LiteralPath $CookieDumpPath))
Write-Host (" - Login lock exists : {0}" -f (Test-Path -LiteralPath $LoginLockPath))
Write-Host (" - Browser session   : {0}" -f (Test-Path -LiteralPath $BrowserSessionPath))
Write-Host (" - Browser profile   : {0}" -f (Test-Path -LiteralPath $BrowserProfilePath))

foreach ($item in $Jobs) {
    Remove-Job -Job $item.Job -Force -ErrorAction SilentlyContinue
}

if ($failed.Count -gt 0) {
    $failedTasks = ($failed | ForEach-Object { $_.Task }) -join ", "
    throw "Some jobs did not complete successfully: $failedTasks"
}

Write-Host ""
Write-Host "All concurrent task jobs completed."
