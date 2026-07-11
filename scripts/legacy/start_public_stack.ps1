param(
    [string]$PrefectApiUrl = "http://127.0.0.1:4200/api",
    [string]$WorkPool = "default-agent-pool",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$FrpcExe = "",
    [string]$FrpcConfig = "",
    [switch]$SkipFrp,
    [switch]$UseSqliteDebug,
    [switch]$ForceRestart
)

& (Join-Path $PSScriptRoot "public_stack.ps1") `
    -Action start `
    -PrefectApiUrl $PrefectApiUrl `
    -WorkPool $WorkPool `
    -BackendPort $BackendPort `
    -FrontendPort $FrontendPort `
    -FrpcExe $FrpcExe `
    -FrpcConfig $FrpcConfig `
    -SkipFrp:$SkipFrp `
    -UseSqliteDebug:$UseSqliteDebug `
    -ForceRestart:$ForceRestart
