# Prefect 数据库使用约定

## 当前唯一允许的数据库

本项目的 Prefect Server、Worker、Deployment 和 Flow Run 统一使用：

```text
PostgreSQL database: prefect_dev
```

`prefect` 库当前不使用。不要对它执行部署、调度、清理、迁移或测试。

## 数据库职责

| 数据库 | 用途 | 当前状态 |
| --- | --- | --- |
| `prefect_dev` | Prefect 调度、Deployment、Flow Run、任务状态和日志元数据 | 唯一允许使用 |
| `prefect` | 旧的正式调度库 | 禁止连接 |
| Dashboard MySQL | 指标、区域、实时值、快照、累计值、目标值 | 业务数据库，不属于 Prefect |

## 连接规则

1. 密码和完整连接串只保存在本机 `scripts/prefect_env_prod.local.ps1`，不得写入文档或提交 Git。
2. 脚本读取基础连接配置后，只派生并使用数据库名 `prefect_dev`。
3. `scripts/prefect_start.ps1` 会校验连接串；数据库名不是 `prefect_dev` 时直接拒绝启动。
4. 日常开发优先使用 `scripts/dev/start.ps1`。
5. 执行 Prefect CLI 前先加载：

```powershell
. .\scripts\dev\env.ps1
$env:PREFECT_API_DATABASE_CONNECTION_URL = $env:AUTO_NOTIFY_PREFECT_DEV_DATABASE_URL
$env:PREFECT_SERVER_DATABASE_CONNECTION_URL = $env:AUTO_NOTIFY_PREFECT_DEV_DATABASE_URL
```

## 启动方式

```powershell
.\scripts\dev\start.ps1
```

或者仅启动 Prefect：

```powershell
. .\scripts\dev\env.ps1
.\scripts\prefect_start.ps1 `
  -Mode both `
  -DatabaseUrl $env:AUTO_NOTIFY_PREFECT_DEV_DATABASE_URL
```

## 发布前检查

连接串不得输出密码，只检查数据库名：

```powershell
. .\scripts\dev\env.ps1
$env:AUTO_NOTIFY_PREFECT_DEV_DATABASE_URL -match "/prefect_dev(?:\?.*)?$"
```

结果必须为 `True`。若不是，停止操作并检查本机配置。

## 变更记录

- 2026-06-24：明确当前只使用 `prefect_dev`，启动脚本增加数据库名强校验。
