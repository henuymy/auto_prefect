# Public Stack Scripts

本文件记录 Prefect + React 管理端 + frp 公网映射的一键启动、关闭和定时任务脚本。

## 脚本清单

- `scripts/public_stack.ps1`
  - 统一入口。
  - `-Action start` 启动整套服务。
  - `-Action stop` 关闭整套服务。

- `scripts/start_public_stack.ps1`
  - 启动快捷入口，内部调用 `public_stack.ps1 -Action start`。

- `scripts/stop_public_stack.ps1`
  - 关闭快捷入口，内部调用 `public_stack.ps1 -Action stop`。

- `scripts/install_public_stack_schedule.ps1`
  - 创建或更新 Windows 每日定时启动/关闭任务。

- `scripts/uninstall_public_stack_schedule.ps1`
  - 删除 Windows 定时任务。

## 启动

```powershell
pwsh -File scripts\public_stack.ps1 -Action start
```

或使用快捷脚本：

```powershell
pwsh -File scripts\start_public_stack.ps1
```

启动内容：

- Prefect Server：`http://127.0.0.1:4200`
- Prefect Worker：默认 work pool 为 `default-agent-pool`
- 后端 API：`http://127.0.0.1:8000`
- React 管理端：`http://127.0.0.1:5173`
- frp 客户端：默认使用 `frp/frpc.exe` 和 `frp/frpc.toml`

启动前会检查端口 `4200`、`8000`、`5173` 是否被占用。如果已占用，脚本会停止启动并提示占用进程。

如需先关闭旧服务再重新启动：

```powershell
pwsh -File scripts\public_stack.ps1 -Action start -ForceRestart
```

或：

```powershell
pwsh -File scripts\start_public_stack.ps1 -ForceRestart
```

如果只需要启动本地服务、不需要公网映射：

```powershell
pwsh -File scripts\public_stack.ps1 -Action start -SkipFrp
```

## 关闭

```powershell
pwsh -File scripts\public_stack.ps1 -Action stop
```

或使用快捷脚本：

```powershell
pwsh -File scripts\stop_public_stack.ps1
```

关闭内容：

- Prefect Server / Worker
- 后端 `8000`
- React/Vite `5173`
- `frpc.exe`
- 相关 PowerShell/cmd 启动窗口

## frp 公网映射

默认读取：

```text
frp/frpc.exe
frp/frpc.toml
```

当前 `frpc.toml` 映射示例：

```toml
serverAddr = "60.205.108.31"
serverPort = 7000

[[proxies]]
name = "test-tcp"
type = "tcp"
localIP = "127.0.0.1"
localPort = 5173
remotePort = 5173
```

公网访问地址：

```text
http://60.205.108.31:5173
```

React/Vite 已配置 `/api` 代理到本机 `http://127.0.0.1:8000`，因此公网只映射 `5173` 时，页面和后端接口都可以通过 Vite 转发访问。

## 定时启动和关闭

创建或更新每日定时任务：

```powershell
pwsh -File scripts\install_public_stack_schedule.ps1 -StartTime "08:30" -StopTime "18:30"
```

默认任务名称：

```text
AutoNotifyPublicStack-Start
AutoNotifyPublicStack-Stop
```

调整时间时，重新执行安装脚本即可覆盖：

```powershell
pwsh -File scripts\install_public_stack_schedule.ps1 -StartTime "07:50" -StopTime "19:10"
```

删除定时任务：

```powershell
pwsh -File scripts\uninstall_public_stack_schedule.ps1
```

查看任务：

```powershell
Get-ScheduledTask -TaskName "AutoNotifyPublicStack-*"
```

## 常用参数

指定 Prefect API：

```powershell
pwsh -File scripts\public_stack.ps1 -Action start -PrefectApiUrl "http://127.0.0.1:4200/api"
```

指定 frp 文件：

```powershell
pwsh -File scripts\public_stack.ps1 -Action start -FrpcExe "C:\path\frpc.exe" -FrpcConfig "C:\path\frpc.toml"
```

Windows Defender 可能将 `frpc.exe` 识别为潜在垃圾软件并隔离。仅使用
[FRP 官方 GitHub Release](https://github.com/fatedier/frp/releases) 下载的文件，并核对 Release
页面提供的 SHA-256。脚本不会自动关闭 Defender 或添加安全排除项；暂不使用公网映射时请加
`-SkipFrp`。

使用 SQLite 调试模式：

```powershell
pwsh -File scripts\public_stack.ps1 -Action start -UseSqliteDebug
```

## 注意事项

- 启动管理端后端时会设置 `PREFECT_API_URL=http://127.0.0.1:4200/api`，避免 Prefect CLI 自动启动临时 server。
- 正式调度模式依赖 `scripts/prefect_env_prod.local.ps1` 中的 `AUTO_NOTIFY_PREFECT_DATABASE_URL`。
- 如果公网访问失败，优先检查：
  - `frpc.exe` 窗口是否正常连接 frps。
  - 云服务器安全组是否放行 `remotePort`。
  - 本机 `5173` 是否能正常访问。
  - React 页面里的 `/api` 请求是否能被 Vite 代理到本机 `8000`。
