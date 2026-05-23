param(
    [string]$TaskPrefix = "AutoNotifyPublicStack"
)

$ErrorActionPreference = "Continue"

$taskNames = @("$TaskPrefix-Start", "$TaskPrefix-Stop")
foreach ($taskName in $taskNames) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "已删除: $taskName"
    } else {
        Write-Host "不存在: $taskName"
    }
}

Write-Host "Done."
