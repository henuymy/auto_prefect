function Invoke-RuntimeStateMigration {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [string]$RuntimeRoot
    )

    $items = @(
        [pscustomobject]@{
            Name = "cookie_dump"
            Source = Join-Path $RepoRoot "runtime\cookies\cookie_dump.json"
            Target = Join-Path $RuntimeRoot "cookies\cookie_dump.json"
            Kind = "file"
        },
        [pscustomobject]@{
            Name = "edge_profile"
            Source = Join-Path $RepoRoot "runtime\browser_session\edge_profile_auto_login"
            Target = Join-Path $RuntimeRoot "browser_session\edge_profile_auto_login"
            Kind = "directory"
        }
    )

    foreach ($item in $items) {
        if (Test-Path -LiteralPath $item.Target) {
            [pscustomobject]@{
                name = $item.Name
                status = "target_exists"
                source = $item.Source
                target = $item.Target
            }
            continue
        }
        if (-not (Test-Path -LiteralPath $item.Source)) {
            [pscustomobject]@{
                name = $item.Name
                status = "source_absent"
                source = $item.Source
                target = $item.Target
            }
            continue
        }

        $stagingPath = $null
        $status = "migration_failed_fresh_login_required"
        try {
            $parent = Split-Path -Parent $item.Target
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
            $stagingPath = Join-Path $parent (
                ".migration-{0}-{1}" -f $item.Name, [guid]::NewGuid().ToString("N")
            )
            if ($item.Kind -eq "directory") {
                Copy-Item -LiteralPath $item.Source -Destination $stagingPath -Recurse
            } else {
                Copy-Item -LiteralPath $item.Source -Destination $stagingPath
            }
            try {
                if ($item.Kind -eq "directory") {
                    [IO.Directory]::Move($stagingPath, $item.Target)
                } else {
                    [IO.File]::Move($stagingPath, $item.Target)
                }
                $status = "copied"
            } catch {
                if (Test-Path -LiteralPath $item.Target) {
                    $status = "target_exists_race"
                } else {
                    throw
                }
            }
        } catch {
            $status = "migration_failed_fresh_login_required"
        } finally {
            if ($stagingPath) {
                $cleanupDeadline = (Get-Date).AddSeconds(2)
                do {
                    try {
                        if (Test-Path -LiteralPath $stagingPath -PathType Container) {
                            [IO.Directory]::Delete($stagingPath, $true)
                        } elseif (Test-Path -LiteralPath $stagingPath -PathType Leaf) {
                            [IO.File]::Delete($stagingPath)
                        }
                    } catch {
                        if ((Get-Date) -lt $cleanupDeadline) {
                            Start-Sleep -Milliseconds 50
                        }
                    }
                } while ((Test-Path -LiteralPath $stagingPath) -and (Get-Date) -lt $cleanupDeadline)
                if (Test-Path -LiteralPath $stagingPath) {
                    $status = "migration_failed_sensitive_staging_cleanup_required"
                }
            }
        }
        [pscustomobject]@{
            name = $item.Name
            status = $status
            source = $item.Source
            target = $item.Target
        }
    }
}
