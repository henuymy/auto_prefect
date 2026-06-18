param(
    [string]$PrefectApiUrl = $env:PREFECT_API_URL,
    [int]$PageSize = 200,
    [switch]$Execute
)

$ErrorActionPreference = "Stop"

if (-not $PrefectApiUrl) {
    $PrefectApiUrl = "http://127.0.0.1:4200/api"
}

$nowUtc = (Get-Date).ToUniversalTime()
$deleted = 0
$matched = 0
$offset = 0

do {
    $body = @{
        flow_runs = @{
            state = @{
                type = @{
                    any_ = @("SCHEDULED")
                }
            }
        }
        sort = "EXPECTED_START_TIME_ASC"
        limit = $PageSize
        offset = $offset
    } | ConvertTo-Json -Depth 8

    $runs = Invoke-RestMethod `
        -Method Post `
        -Uri "$PrefectApiUrl/flow_runs/filter" `
        -ContentType "application/json" `
        -Body $body

    foreach ($run in $runs) {
        if (-not $run.expected_start_time) {
            continue
        }
        $expected = [datetime]::Parse($run.expected_start_time).ToUniversalTime()
        if ($expected -ge $nowUtc) {
            continue
        }

        $matched += 1
        Write-Host (
            "过期 SCHEDULED: {0} {1} expected_start_time={2}" -f `
                $run.id, $run.name, $run.expected_start_time
        )

        if ($Execute) {
            Invoke-RestMethod `
                -Method Delete `
                -Uri "$PrefectApiUrl/flow_runs/$($run.id)" | Out-Null
            $deleted += 1
        }
    }

    $offset += $runs.Count
} while ($runs.Count -eq $PageSize)

if ($Execute) {
    Write-Host "已删除过期 SCHEDULED flow runs: $deleted"
} else {
    Write-Host "Dry-run: 匹配到过期 SCHEDULED flow runs: $matched"
    Write-Host "如需删除，请加 -Execute"
}
