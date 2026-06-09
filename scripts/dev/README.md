# 开发环境脚本

本目录只用于本机开发调度，固定使用：

```text
数据库：prefect_dev
Work Pool：dev-agent-pool
Prefect API：http://127.0.0.1:4200/api
```

它与正式数据库 `prefect` 和正式 Work Pool `default-agent-pool` 隔离。

## 启动

启动 Prefect Server、开发 Worker、后端和前端：

```powershell
pwsh -File scripts\dev\start.ps1
```

端口已占用时先关闭再启动：

```powershell
pwsh -File scripts\dev\start.ps1 -ForceRestart
```

只启动 Prefect Server 和开发 Worker：

```powershell
pwsh -File scripts\dev\start.ps1 -SkipAdmin
```

## 查看状态

```powershell
pwsh -File scripts\dev\status.ps1
```

## 停止

```powershell
pwsh -File scripts\dev\stop.ps1
```

## 注意

- 不要用默认 `scripts/public_stack.ps1 -Action start` 进行开发，它默认连接正式数据库。
- 开发 Deployment 必须发布到 `dev-agent-pool`。
- 开发配置应使用企业微信测试群 Webhook。
- `scripts/prefect_env_prod.local.ps1` 仍保存正式数据库连接，本目录只根据它派生
  `prefect_dev` 地址，不复制密码。
