$ErrorActionPreference = "Stop"

& (Join-Path (Split-Path -Parent $PSScriptRoot) "stop.ps1")
