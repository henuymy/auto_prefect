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

## 数据驾驶舱：触发与会话

第一阶段 Flow 只执行以下步骤：

```text
创建批次 -> 获取驾驶舱采集锁 -> city_ops 探活/重登 -> 会话就绪
```

本地直接试跑：

```powershell
python -c "from flows.dashboard_session_flow import dashboard_session_flow; print(dashboard_session_flow(trigger_type='MANUAL'))"
```

发布到开发 Work Pool：

```powershell
python -m prefect deploy --name dashboard-session --pool dev-agent-pool
```

发布后可通过后端手动提交，接口立即返回 Prefect Flow Run ID：

```text
POST /api/dashboard/collect
POST /api/dashboard/collect?force_refresh=true
```

批次运行记录暂存于：

```text
runtime/dashboard/collection_runs/
```

本阶段不会请求指标接口、写驾驶舱 MySQL，也不会调用 Excel、比对或企业微信流程。

## 驾驶舱 MySQL 配置

复制本机配置模板：

```powershell
Copy-Item scripts\dashboard_mysql_env.example.ps1 scripts\dashboard_mysql_env.local.ps1
```

填写 `DASHBOARD_MYSQL_*` 后，启动脚本会自动加载该文件。连接检查可通过：

```text
GET /api/status
```

返回值中的 `dashboard_mysql` 只展示主机、端口、库名、用户和连接状态，不返回密码。

也可以在已加载环境变量的 PowerShell 中直接检查：

```powershell
. scripts\dashboard_mysql_env.ps1
python -m infrastructure.dashboard_mysql
```

Alembic 使用独立配置：

```powershell
python -m alembic -c alembic_dashboard.ini current
```

创建或升级 `collection_run`：

```powershell
. scripts\dashboard_mysql_env.ps1
python -m alembic -c alembic_dashboard.ini upgrade head
```

该迁移只操作 `DASHBOARD_MYSQL_DATABASE` 指定的独立驾驶舱数据库。

## 注意

- 不要用默认 `scripts/public_stack.ps1 -Action start` 进行开发，它默认连接正式数据库。
- 开发 Deployment 必须发布到 `dev-agent-pool`。
- 开发配置应使用企业微信测试群 Webhook。
- `scripts/prefect_env_prod.local.ps1` 仍保存正式数据库连接，本目录只根据它派生
  `prefect_dev` 地址，不复制密码。
