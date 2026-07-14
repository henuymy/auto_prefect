$RepoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
. (Join-Path $RepoRoot "scripts\lib\runtime_config.ps1")
if (Import-ProjectRuntimeConfig) {
    return
}
throw "缺少运行配置。请创建 config\runtime.local.json。"
