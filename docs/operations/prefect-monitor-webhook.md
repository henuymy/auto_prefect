# Prefect 通报监控 Webhook

## 目的

将通报任务的 Flow Run 状态变化以低延迟方式推送到监控页面。Prefect 是运行事实来源，MySQL 保存页面需要的持久化投影，WebSocket 只向已连接页面广播已提交的增量。

当前代码提供接收端，但不会自动在 Prefect 中创建 Automation。每个环境必须由有权限的运维人员单独配置，并使用该环境的本地密钥。

## 前置条件

- FastAPI 已运行，且 Prefect Server 能访问其回调地址。
- 本机被忽略的 `config/runtime.local.json` 包含 `monitor.prefect_webhook_secret`。
- 后端进程已从运行配置导出 `PREFECT_MONITOR_WEBHOOK_SECRET`。
- 已执行 `alembic -c alembic_dashboard_v2.ini upgrade head`，使监控表迁移到最新版本。
- 先通过 `GET /api/live` 确认 `monitorEvents` 存在，且包含 `lastAcceptedAt`、`lastReconciledAt` 与 `lastErrorCategory`。初次启动时前两项为 `null` 是正常状态。

## 创建 Automation

在 Prefect UI 或官方 Automation API 中创建 Flow Run 状态变化 Automation：

1. 过滤 Flow 为 `auto-notify-flow`。
2. 过滤 Deployment 名称以 `notify-` 开头。
3. 状态事件包含 Scheduled、Running、Completed、Failed、Crashed、Cancelled。
4. 动作为 HTTP Webhook，目标地址为 `https://<受控后端地址>/api/monitor/events/prefect`。
5. 请求头名为 `X-Prefect-Monitor-Secret`，请求头值使用当前机器的本地密钥。

不要把真实密钥粘贴到文档、截图、测试数据或版本库文件中。

Webhook 接收端只接受 Prefect Flow Run 事件。事件必须包含非空的 `id`、`occurred` 和资源 ID `prefect.flow-run.<flow-run-id>`；同一个事件 ID 可以重复投递，服务端会返回成功而不重复写入或广播。非通报任务也会被安全确认，但不会进入通报监控视图。

状态以事件发生时间为准。较旧的 Running 事件到达时，不会把已经完成或失败的记录回退；`FAILED` 和 `CRASHED` 都会显示为“失败”。

## 验收

使用受控通报 Flow Run 依次产生 Scheduled、Running、Completed 状态，并额外执行一次 Failed 或 Crashed 用例。每次状态变化后确认：

- Webhook 返回 202。
- `GET /api/live` 的 `monitorEvents.lastAcceptedAt` 更新。
- 监控页面只更新对应通报任务。
- Scheduled 变为 Running 后，待执行数量减少。
- Session Keeper 与 Dashboard 任务不进入运行记录、时间线或待执行队列。
- 运行记录和时间线只出现最近 30 天的成功、运行中和失败通报；Scheduled 只出现在“待执行（全局）”抽屉，且显示“尚未开始”或真实下一环节。
- 点击运行记录后，详情抽屉可以读取 Task Run 与脱敏日志；临时停止 Prefect API 时，已同步摘要仍可显示，并显示详情不可用提示。
- “页面实时：已连接”“上游事件”和“最近对账”分别变化；不能以浏览器 WebSocket 在线代替 Webhook 或对账成功。

记录至少 20 次从 Prefect 状态变化到页面可见的耗时；P95 不超过 5 秒才可视为首期实时监控达标。在没有完成该测量前，只能称接收端和页面链路已部署，不能称已达到实时 SLA。

## 故障处理

- 401：检查后端进程是否读取了本地密钥，以及 Automation 请求头是否使用同一值。
- 422：检查 Automation 是否发送 Prefect Flow Run 事件，且事件包含 `id`、`occurred` 与 `prefect.flow-run.<id>` 资源 ID。
- 503：检查 Prefect API 和 MySQL；事件处理器会在首次收到 Flow Run 时读取真实 Deployment 与 Flow 身份。
- 页面断线：浏览器会重连并重新获取 MySQL 快照；REST 对账每 5 分钟补漏。
- 上游事件长时间未变化：先确认该时间段确有符合过滤条件的通报状态变化，再检查 Automation 的过滤条件、目标地址和请求头。低频通报在没有状态变化时不会产生新事件。
- 最近对账停滞：检查 FastAPI 生命周期日志、Prefect API 可用性和 MySQL 连通性；页面仍会显示最后一次成功快照。
- 多进程或多实例 FastAPI：当前进程内 Hub 不能跨进程广播。上线扩容前必须接入 Redis Pub/Sub 或等价共享消息总线。
