# Prefect 监控实时 Webhook 设计
> **归档状态：** 本文记录当时的需求、设计或实施计划；当前有效的运行契约以 [运行监控中心设计](../../run-monitoring-center-design.md) 和 [Webhook 运维手册](../../operations/prefect-monitor-webhook.md) 为准。
>


## 目标

让通报任务的状态变化在 95% 的情况下于 5 秒内显示到监控页面；不读取或写入 Prefect PostgreSQL 内部表，首期也不引入 Redis。

## 范围与约束

- Prefect 是 Flow Run 状态的唯一权威来源。
- MySQL 是项目自有、可长期保存的监控投影库。
- 通报任务沿用既有规则：Flow 必须为 `auto-notify-flow`，Deployment 名称必须以 `notify-` 开头。
- 监控页面仍只展示通报的运行记录和通报的待执行任务。
- 首期只运行一个 FastAPI 进程，因此进程内直接广播 WebSocket 是有效的实现方式。
- 现有 Prefect REST 同步保留为补漏与对账机制，不再承担主更新链路。

## 总体架构

Prefect Automation 在通报 Flow Run 状态变化时向新增的 FastAPI 认证端点发送事件。端点验证共享密钥、识别重复事件、将状态写入 MySQL；事务提交成功后，立即向已连接的监控 WebSocket 客户端广播增量变化。

```text
Prefect Automation
  -> POST /api/monitor/events/prefect
  -> 校验来源与事件身份
  -> MySQL 事务：monitor_runs + monitor_events
  -> FastAPI 进程内 WebSocket 广播
  -> 监控页面局部更新
```

当事件对应的运行记录尚未出现在监控库中，端点才通过 Prefect 官方 REST API 查询该 Flow Run 和 Deployment。这样既保留真实通报部署身份，也避免每个事件都执行一次全量 `flow_runs/filter` 查询。

## 事件契约

端点接收 Prefect Automation 的事件载荷，并要求请求携带从本地运行配置加载的专用密钥请求头。Prefect 事件 ID 是幂等键；迁移会给 `monitor_events` 新增可为空且唯一的 `source_event_id`。重复投递直接返回成功，但不重复改变运行状态。

状态更新按事件发生时间排序。过期事件可保留在历史事件中，但不能覆盖 `monitor_runs` 中更新的运行中或终态。无法识别的任务与非通报任务会被确认接收，但不会进入通报监控视图。

MySQL 事务提交后，广播使用带版本的消息类型，并且只包含变更的运行记录和由此导出的汇总、待执行队列计数。新的 WebSocket 连接先收到完整快照；断线重连时，前端先通过既有 REST 快照接口恢复，再继续消费之后的增量消息。

## REST 对账

现有 Prefect REST 适配器改为有界的补漏任务：每 5 分钟查询一个存在时间重叠的近期范围，将 Prefect 的权威状态重新对账到 MySQL。每天执行一次 30 天范围校验，以覆盖长期待执行任务并确认历史保留正确。

对账引起的状态变更使用同一套 MySQL 持久化方法，并在提交后发布相同的 WebSocket 增量事件。原有“每 15 秒全量拉取 200 条”的循环会在事件接收稳定后移除，不能无限期与 5 分钟补漏任务并行。

## 安全与运行状态

- Prefect Automation 使用未纳入版本控制的专用密钥请求头；端点绝不记录密钥值或未脱敏的敏感事件内容。
- `/api/live` 增加监控事件接收健康状态：最后成功接收时间、最后对账时间、最近错误类别。
- 指标记录收到、重复、拒绝、失败、已广播事件的数量，以及事件接收到广播完成的延迟。
- 端点或浏览器连接短暂不可用时，MySQL 历史仍由 REST 对账修复；页面通过首次完整快照恢复状态。

## 扩展边界

进程内广播只能通知连接到同一 FastAPI 进程的 WebSocket 客户端。当部署为多个 Uvicorn Worker 或多台应用服务器时，再将该单一广播边界替换为 Redis Pub/Sub 的跨进程扇出，并使用 Redis Streams 提供短期事件回放。

即使引入 Redis，MySQL 仍是持久化数据源与 API 契约来源，不需要重新设计监控表结构。

## 验证标准

- 单元测试覆盖认证、幂等、状态乱序、通报过滤，以及“事务提交后才广播”。
- 集成测试向 Webhook 端点发送 Automation 事件，验证 MySQL 投影与 WebSocket 增量消息。
- 浏览器验收覆盖通报任务的完成、失败、运行中、待执行、重复投递、断线重连及 REST 对账补漏。
- 从 Prefect 测试状态变化到页面状态渲染测量端到端延迟；仅当运行环境中 P95 不超过 5 秒时，才视为首期目标达成。
