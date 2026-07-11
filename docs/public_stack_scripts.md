# Public Stack Scripts

本文件记录 Prefect + 管理端后端 + React 前端 + frp 公网映射的一键启动、关闭、定时任务和排障方式。

## 脚本清单

- `scripts/public_stack.ps1`
  - 统一入口。
  - `-Action start` 启动整套服务。
  - `-Action stop` 关闭整套服务。

- `scripts/start_public_stack.ps1`
  - 启动快捷入口。
  - 内部调用 `public_stack.ps1 -Action start`。

- `scripts/stop_public_stack.ps1`
  - 关闭快捷入口。
  - 内部调用 `public_stack.ps1 -Action stop`。

- `scripts/prefect_start.ps1`
  - 启动 Prefect Server 和 Worker。
  - 正式调度模式下会先检查 PostgreSQL 是否可连接。
  - detached 模式下 Worker 会自动守护重启。

- `scripts/prefect_stop.ps1`
  - 停止 Prefect Server / Worker。
  - 清理 SQLite 调试模式残留的 WAL/SHM 文件。

- `scripts/install_public_stack_schedule.ps1`
  - 创建或更新 Windows 每日定时启动/关闭任务。

- `scripts/uninstall_public_stack_schedule.ps1`
  - 删除 Windows 定时任务。

## 日常启动

推荐使用快捷脚本：

```powershell
pwsh -File scripts\start_public_stack.ps1
```

等价于：

```powershell
pwsh -File scripts\public_stack.ps1 -Action start
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
pwsh -File scripts\start_public_stack.ps1 -ForceRestart
```

等价于：

```powershell
pwsh -File scripts\public_stack.ps1 -Action start -ForceRestart
```

## 日常关闭

推荐使用快捷脚本：

```powershell
pwsh -File scripts\stop_public_stack.ps1
```

等价于：

```powershell
pwsh -File scripts\public_stack.ps1 -Action stop
```

关闭内容：

- Prefect Server / Worker
- 后端 `8000`
- React/Vite `5173`
- `frpc.exe`
- 相关 PowerShell/cmd 启动窗口

## Prefect 正式调度

正式调度模式依赖 PostgreSQL。连接串放在本机文件：

```text
scripts/prefect_env_prod.local.ps1
```

需要设置：

```powershell
$env:AUTO_NOTIFY_PREFECT_DATABASE_URL = "postgresql+asyncpg://user:password@host:5432/prefect"
```

启动 `prefect_start.ps1` 时，正式模式会先执行 PostgreSQL 预检：

```text
检查 Prefect PostgreSQL 连接...
  postgres_ok
```

如果预检失败，Prefect Server / Worker 不会继续启动。先检查：

```powershell
. .\scripts\prefect_env_prod.ps1
Test-NetConnection 60.205.108.31 -Port 5432
```

如果端口不通，检查云服务器 PostgreSQL、服务器防火墙、安全组、本机网络。

如果端口通但登录失败，检查 `AUTO_NOTIFY_PREFECT_DATABASE_URL` 的用户名、密码、数据库名和 PostgreSQL 访问权限。

## Worker 自动重启

`public_stack.ps1` 会以 detached 模式调用：

```powershell
pwsh -File scripts\prefect_start.ps1 -Mode both -Detached
```

detached 模式下，Prefect Worker 会自动守护重启。如果 Prefect API 或数据库短暂返回 500，导致 Worker 退出，Worker 启动窗口会等待 30 秒后重新拉起。

手工排查 Worker 时，使用前台模式：

```powershell
pwsh -File scripts\prefect_start.ps1 -Mode worker
```

前台模式不会自动重启，方便观察完整报错和手动 Ctrl+C 停止。

## 常见故障

### Prefect Worker 报 500 后退出

典型日志：

```text
get_scheduled_flow_runs
500 Internal Server Error
Service exceeded error threshold
```

通常原因不是业务配置，而是 Prefect Server 或 Prefect 元数据库暂时异常。

先检查数据库端口：

```powershell
. .\scripts\prefect_env_prod.ps1
Test-NetConnection 60.205.108.31 -Port 5432
```

然后重启整套服务：

```powershell
pwsh -File scripts\start_public_stack.ps1 -ForceRestart
```

注意：Worker 恢复后可能立刻接手已经到点的 scheduled runs，并触发实际通报发送。

### Prefect Server 日志出现 asyncpg TimeoutError

典型日志：

```text
asyncpg ... TimeoutError
schedule_recent_deployments
monitor_expired_pauses
```

含义是 Prefect Server 的后台服务连接 PostgreSQL 超时。

处理顺序：

```powershell
. .\scripts\prefect_env_prod.ps1
Test-NetConnection 60.205.108.31 -Port 5432
pwsh -File scripts\start_public_stack.ps1 -ForceRestart
```

如果反复出现，优先检查远程 PostgreSQL 所在机器负载、网络稳定性、数据库连接数和防火墙策略。

### work pool 显示 NOT_READY

`default-agent-pool` 为 `NOT_READY` 表示当前没有健康 Worker 在拉取任务。

查看：

```powershell
. .\scripts\prefect_env_prod.ps1
python -m prefect work-pool inspect default-agent-pool
```

恢复：

```powershell
pwsh -File scripts\prefect_start.ps1 -Mode worker
```

或重启整套：

```powershell
pwsh -File scripts\start_public_stack.ps1 -ForceRestart
```

### 端口被占用

启动时如果提示 `4200`、`8000`、`5173` 被占用，可直接强制重启：

```powershell
pwsh -File scripts\start_public_stack.ps1 -ForceRestart
```

只关闭不启动：

```powershell
pwsh -File scripts\stop_public_stack.ps1
```

## SQLite 调试模式

本地临时调试可以使用 SQLite：

```powershell
pwsh -File scripts\start_public_stack.ps1 -UseSqliteDebug
```

或只启动 Prefect：

```powershell
pwsh -File scripts\prefect_start.ps1 -Mode both -Detached -UseSqliteDebug
```

SQLite 调试模式使用：

```text
runtime/prefect_home/prefect.db
```

注意：

- SQLite 只适合本地手动测试。
- 不建议用于长期调度。
- 长期调度请使用 PostgreSQL。

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

如果公网访问失败，优先检查：

- `frpc.exe` 窗口是否正常连接 frps。
- 云服务器安全组是否放行 `remotePort`。
- 本机 `5173` 是否能正常访问。
- React 页面里的 `/api` 请求是否能被 Vite 代理到本机 `8000`。

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

指定 work pool：

```powershell
pwsh -File scripts\public_stack.ps1 -Action start -WorkPool "default-agent-pool"
```

指定后端和前端端口：

```powershell
pwsh -File scripts\public_stack.ps1 -Action start -BackendPort 8000 -FrontendPort 5173
```

指定 frp 文件：

```powershell
pwsh -File scripts\public_stack.ps1 -Action start -FrpcExe "C:\path\frpc.exe" -FrpcConfig "C:\path\frpc.toml"
```

## 注意事项

- 启动管理端后端时会设置 `PREFECT_API_URL=http://127.0.0.1:4200/api`，避免 Prefect CLI 自动启动临时 server。
- 正式调度模式依赖 `scripts\prefect_env_prod.local.ps1` 中的 `AUTO_NOTIFY_PREFECT_DATABASE_URL`。
- `start_public_stack.ps1` 不需要单独改动 Worker 自动重启参数；它通过 `public_stack.ps1` 调用 `prefect_start.ps1`，会自动继承 detached 模式的 Worker 守护逻辑。
- 重启 Worker 后，已经到点的 scheduled runs 可能会立即执行。
