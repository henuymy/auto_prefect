$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "env.ps1")

& (Join-Path $ScriptsDir "public_stack.ps1") `
    -Action stop `
    -BackendPort $DevBackendPort `
    -FrontendPort $DevFrontendPort

