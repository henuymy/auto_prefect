param(
    [string]$StartTime = "08:30",
    [string]$StopTime = "18:30",
    [string]$TaskPrefix = "AutoNotifyPublicStack",
    [string]$PowerShellExe = "pwsh.exe"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$StackScript = Join-Path $PSScriptRoot "..\run.ps1"

if (-not (Test-Path -LiteralPath $StackScript)) {
    throw "未找到脚本: $StackScript"
}

function New-StackTask {
    param(
        [string]$Name,
        [string]$ActionName,
        [string]$Time
    )

    $argument = "-NoProfile -ExecutionPolicy Bypass -File `"$StackScript`" -Action $ActionName"
    $action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $argument -WorkingDirectory $RepoRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $Time
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask `
        -TaskName $Name `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "Auto Notify public stack $ActionName" `
        -Force | Out-Null
}

$startTaskName = "$TaskPrefix-Start"
$stopTaskName = "$TaskPrefix-Stop"

New-StackTask -Name $startTaskName -ActionName "start" -Time $StartTime
New-StackTask -Name $stopTaskName -ActionName "stop" -Time $StopTime

Write-Host "已创建/更新每日定时任务："
Write-Host "启动: $startTaskName -> $StartTime"
Write-Host "关闭: $stopTaskName  -> $StopTime"
Write-Host ""
Write-Host "查看任务："
Write-Host "Get-ScheduledTask -TaskName '$TaskPrefix-*'"
