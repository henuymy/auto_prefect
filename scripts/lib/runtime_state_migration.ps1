function Invoke-RuntimeStateMigration {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [string]$RuntimeRoot
    )

    # This is a one-time, destructive cutover. Startup scripts deliberately do not call it.
    foreach ($directory in @(
        $RuntimeRoot,
        (Join-Path $RuntimeRoot "session\locks"),
        (Join-Path $RuntimeRoot "modules"),
        (Join-Path $RuntimeRoot "flow")
    )) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    $items = @(
        [pscustomobject]@{ Name = "cookie_dump"; Sources = @(
            (Join-Path $RuntimeRoot "cookies\cookie_dump.json"),
            (Join-Path $RepoRoot "runtime\cookies\cookie_dump.json")
        ); Target = Join-Path $RuntimeRoot "session\cookie_dump.json" },
        [pscustomobject]@{ Name = "session_health"; Sources = @(
            (Join-Path $RuntimeRoot "session\session_state.json"),
            (Join-Path $RepoRoot "runtime\session\session_state.json")
        ); Target = Join-Path $RuntimeRoot "session\session-health.json" },
        [pscustomobject]@{ Name = "browser_session"; Sources = @(
            (Join-Path $RuntimeRoot "browser_session\session.json"),
            (Join-Path $RepoRoot "runtime\browser_session\session.json")
        ); Target = Join-Path $RuntimeRoot "session\browser-session.json" },
        [pscustomobject]@{ Name = "browser_profile"; Sources = @(
            (Join-Path $RuntimeRoot "browser_session\edge_profile_auto_login"),
            (Join-Path $RepoRoot "runtime\browser_session\edge_profile_auto_login")
        ); Target = Join-Path $RuntimeRoot "session\browser-profile" }
    )

    foreach ($item in $items) {
        $source = $item.Sources | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $source) {
            [pscustomobject]@{ name = $item.Name; status = "source_absent"; target = $item.Target }
            continue
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $item.Target) | Out-Null
        if (Test-Path -LiteralPath $item.Target) {
            Remove-Item -LiteralPath $source -Recurse -Force
            [pscustomobject]@{ name = $item.Name; status = "source_removed_target_present"; target = $item.Target }
            continue
        }
        Move-Item -LiteralPath $source -Destination $item.Target -Force
        [pscustomobject]@{ name = $item.Name; status = "moved"; target = $item.Target }
    }

    foreach ($legacy in @(
        (Join-Path $RuntimeRoot "cookies"),
        (Join-Path $RuntimeRoot "browser_session"),
        (Join-Path $RepoRoot "runtime")
    )) {
        if (Test-Path -LiteralPath $legacy) {
            Remove-Item -LiteralPath $legacy -Recurse -Force
        }
    }
}
