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
            [pscustomobject]@{ name = $item.Name; status = "target_exists"; target = $item.Target }
            continue
        }
        if (-not (Test-Path -LiteralPath $item.Source)) {
            [pscustomobject]@{ name = $item.Name; status = "source_absent"; target = $item.Target }
            continue
        }

        $parent = Split-Path -Parent $item.Target
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
        $stagingPath = Join-Path $parent (
            ".migration-{0}-{1}" -f $item.Name, [guid]::NewGuid().ToString("N")
        )
        $published = $false
        try {
            if ($item.Kind -eq "directory") {
                Copy-Item -LiteralPath $item.Source -Destination $stagingPath -Recurse
            } else {
                Copy-Item -LiteralPath $item.Source -Destination $stagingPath
            }
            try {
                Move-Item -LiteralPath $stagingPath -Destination $item.Target -ErrorAction Stop
                $published = $true
            } catch {
                if (-not (Test-Path -LiteralPath $item.Target)) {
                    throw
                }
            }
        } finally {
            if (Test-Path -LiteralPath $stagingPath -PathType Container) {
                [IO.Directory]::Delete($stagingPath, $true)
            } elseif (Test-Path -LiteralPath $stagingPath -PathType Leaf) {
                [IO.File]::Delete($stagingPath)
            }
        }
        if ($published) {
            [pscustomobject]@{ name = $item.Name; status = "copied"; target = $item.Target }
        } else {
            [pscustomobject]@{ name = $item.Name; status = "target_exists"; target = $item.Target }
        }
    }
}
