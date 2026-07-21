# Prefect Deployment 单一声明设计

## 目标

将当前 Prefect Server 中的 19 个 Deployment 全部纳入 `prefect.yaml` 管理，使迁移机器或重建 Prefect 元数据库后，一次执行 `python -X utf8 -m prefect deploy --all` 即可恢复完整部署集合。

## 范围

- 保留 6 个系统 Deployment：`session-keeper` 与 5 个驾驶舱 Deployment。
- 将 13 个 `notify-*` 通报 Deployment 加入 `prefect.yaml`。
- 所有通报 Deployment 使用 `flows/notify_single_flow.py:auto_notify_flow` 和 `windows-notify-pool`。
- 将通报的 `config_path` 固定为仓库相对路径 `config/tasks/<名称>.json`，不再保存当前桌面目录的绝对路径。
- 删除当前 Prefect API 中的全部 19 个 Deployment，再以 YAML 作为唯一来源重新发布。

## 调度保留规则

迁移以远端 Deployment 为准，逐条保留 Cron、`Asia/Shanghai` 时区和启用状态：

- `dashboard-collection` 和 `session-keeper` 的现有 Cron 保留但保持暂停。
- `dashboard-indicator-sync` 与 `dashboard-v2-partition-maintenance` 保持启用。
- `dashboard-daily-acc`、`dashboard-monthly`、`notify-升档市公司通报`、`notify-流量市公司通报`、`notify-离网市公司通报`、`notify-降档市公司通报` 不声明 Cron。
- 其余 9 个通报 Deployment 保留现有 Cron，但保持暂停。

这样重新发布不会立即产生新的通报或会话任务；是否启用调度由后续明确的运维操作决定。

## 执行与验证

1. 在删除前校验 YAML 可解析，且声明名称恰好为 19 个。
2. 使用 Prefect 官方客户端删除当前 API 中的所有 Deployment，并逐项记录删除结果。
3. 再次读取 API，确认 Deployment 数量为 0。
4. 不在本次操作中重新发布；发布将在项目移至稳定目录并完成生产配置后执行。

数据库、Flow Run 历史、Work Pool、Automation 和本地 `runtime.local.json` 不属于本次删除范围。
