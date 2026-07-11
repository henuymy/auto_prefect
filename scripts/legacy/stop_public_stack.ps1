param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$FrpcExe = "",
    [switch]$KillAutoNotifyPython = $true
)

& (Join-Path $PSScriptRoot "public_stack.ps1") `
    -Action stop `
    -BackendPort $BackendPort `
    -FrontendPort $FrontendPort `
    -FrpcExe $FrpcExe `
    -KillAutoNotifyPython:$KillAutoNotifyPython
