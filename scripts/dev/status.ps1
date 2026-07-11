$ErrorActionPreference = "Stop"

& (Join-Path (Split-Path -Parent $PSScriptRoot) "status.ps1")
