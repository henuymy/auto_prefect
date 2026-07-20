# 运行监控中心设计

## 目标与范围

运行监控中心为通报任务提供可读、可追溯的运行视图。Prefect 继续是编排、调度、重试和原始技术日志的权威系统；监控中心不替代 Prefect UI，也不暴露 Prefect UI 跳转、管理令牌或重试、取消、暂停等编排操作。

页面只展示真实通报部署身份对应的 Flow Run：Flow 必须为 `auto-notify-flow`，Deployment 名称必须以 `notify-` 开头，目标标识为 `report-<deployment UUID>`。Session Keeper、驾驶舱采集和网页后台操作不进入运行记录、时间线或待执行队列。

运行历史使用滚动 30 天窗口并保留 30 天。运行记录与时间线仅显示 `succeeded`、`running`、`failed`；`scheduled` 只进入独立的待执行队列，避免未来计划与已执行记录混杂。

## 数据边界

| 系统 | 职责 | 访问方式 |
| --- | --- | --- |
| Prefect PostgreSQL | Flow Run、Deployment、Flow、Task Run 和原始日志的权威来源 | 仅通过 Prefect 官方 REST API 读取；不直接读写内部表 |
| 驾驶舱 MySQL | 页面需要的通报运行投影、步骤和脱敏事件 | Alembic 管理的项目自有 `monitor_` 表 |
| FastAPI | 认证事件接入、投影查询、详情按需读取和单进程实时广播 | REST 与 WebSocket |
| React 监控页 | 快照展示、增量合并、分页、时间线和详情抽屉 | REST 首屏加 WebSocket 增量 |

MySQL 表均属于应用数据，不能与驾驶舱指标表或 Prefect 内部表混用：

- `monitor_runs` 保存通报运行主体、真实 `target_kind`/`target_id`、当前状态、业务与技术失败摘要以及 `state_occurred_at`。
- `monitor_steps` 保存页面可展示的步骤状态与时间。
- `monitor_events` 保存脱敏事件，`source_event_id` 是 Prefect Automation 事件的幂等键，`stream_sequence` 用于稳定排序。

迁移链为 `20260718_0002_monitor_center`、`20260719_0003_monitor_target_identity`、`20260719_0004_monitor_event_idempotency`。已结束的通报记录及其级联步骤、事件由驾驶舱维护任务清理，默认保留 30 天；运行中的记录不会因保留任务被删除。

## 实时链路与对账

```text
Prefect Automation
  -> POST /api/monitor/events/prefect
  -> 校验 X-Prefect-Monitor-Secret、事件格式和通报身份
  -> Prefect REST 补齐 Flow Run / Deployment / Flow 的真实身份
  -> MySQL 单事务写入 monitor_runs + monitor_events
  -> 事务提交成功后，FastAPI WebSocket Hub 广播 run.updated
  -> 已连接浏览器局部替换对应运行、汇总和待执行队列
```

Webhook 是低延迟主链路。事件以 Prefect event ID 去重，按 `occurred` 时间排序；重复投递和晚到的旧状态不会覆盖已保存的新状态。非通报事件被安全确认但不写入通报视图。

浏览器打开页面时，先通过 `GET /api/monitor/snapshot` 读取 MySQL 快照，再建立 `WS /api/monitor/stream`。新连接收到一条 `snapshot`；后续只接收提交后的 `run.updated`，前端按 `runId` 局部合并，避免每次状态变化重拉完整记录。连接断开后会重连；重连后的初始快照负责恢复可能漏掉的数据。

Webhook 不能取代对账。FastAPI 生命周期中启动 `MonitorSyncLoop`，默认每 5 分钟经 Prefect 官方 REST API 拉取按预计开始时间排序的最近 200 条运行，并将确有变化的记录发布到 WebSocket。当前实现尚未包含每日 30 天全量校验；若 200 条窗口不足以覆盖高频运行或长期待执行项，应先扩展适配器和回归测试。Webhook、MySQL 或 Prefect 短暂不可用时，页面保留上一次成功快照，下一次 REST 对账负责补漏。

当前 Hub 是单 FastAPI 进程内广播，因而“页面实时：已连接”只表示浏览器与该进程的 WebSocket 连接正常。它不表示 Prefect Automation 正常，也不表示对账成功。页面必须将“页面实时”“上游事件”“最近对账”分开显示。若改为多个 Uvicorn Worker 或多台应用服务器，须将广播改为 Redis Pub/Sub，并使用 Redis Streams 或等价机制处理短期回放；MySQL 仍是持久化投影和 API 查询源。

## 接口契约

| 接口 | 用途 | 关键语义 |
| --- | --- | --- |
| `GET /api/monitor/snapshot` | 首屏与重连快照 | 返回 30 天通报运行、全局通报待执行队列、快照时间和上游状态 |
| `GET /api/monitor/summary` | 轻量汇总 | 返回快照中的汇总与更新时间 |
| `GET /api/monitor/runs` | 查询接口 | 支持来源、状态、任务和时间筛选；页面主视图以 snapshot 为准 |
| `GET /api/monitor/scheduled` | 待执行查询 | 返回通报 Scheduled 队列及每项 `nextStep` |
| `GET /api/monitor/runs/{run_id}` | 详情抽屉 | Prefect 来源按需查询 Task Run 与日志；不可用时返回已有摘要和降级提示 |
| `POST /api/monitor/events/prefect` | Automation 接入 | 需要 `X-Prefect-Monitor-Secret`，成功、重复和过期事件均返回 202；认证失败 401，格式错误 422 |
| `WS /api/monitor/stream` | 页面增量 | 初始 `snapshot` 加提交后 `run.updated`；不轮询推送完整快照 |
| `GET /api/live` | 进程存活和监控上游状态 | `monitorEvents` 含最后事件接收、最后对账与最近错误类别 |

待执行条目的 `nextStep` 是明确契约：未开始的计划显示“尚未开始”，不得将 `Scheduled` 误显示为已完成或虚构的执行步骤。FAILED 与 CRASHED 必须映射为页面“失败”，不能被后续对账覆盖为完成。

## 页面与交互

顶部展示“最近 30 天运行概览”、页面 WebSocket 状态、数据快照时间、上游事件时间和最近对账时间。它们各自说明不同链路，不能合并为笼统的“已连接”。

运行记录提供对象、触发方式、状态和日期筛选，支持每页 10、20、50 条。状态汇总中的成功、运行中、失败打开相应筛选结果；“待执行（全局）”打开独立通报 Scheduled 队列，不受筛选影响。时间线按日期分组，可逐日收起展开，并按批次加载更多。

点击一条运行记录后：

- 业务概览默认展示状态、耗时、步骤和失败摘要，优先让通报人员判断是否需要处理。
- 技术详情展示脱敏后的步骤消息、技术摘要和按级别筛选的日志，仅适合受控内部环境；它不是 Prefect UI 的替代品。
- 对于失败运行，摘要优先从 Prefect `state.message`、失败 Task Run 和 ERROR 日志生成。Prefect 详情读取失败时，抽屉保留 MySQL 中已有摘要并明确显示“详情暂不可用”。
- 三种抽屉均采用 `role="dialog"`、`aria-modal="true"`，打开后初始聚焦关闭按钮；`Escape` 关闭，Tab 与 Shift+Tab 在抽屉内部循环，关闭后焦点返回原触发元素。

## 安全与运行约束

- Webhook 密钥只保存在被 Git 忽略的 `config/runtime.local.json`，由启动配置导出 `PREFECT_MONITOR_WEBHOOK_SECRET`；不得写入文档、测试样例、日志、截图或版本库。
- 事件、失败摘要和日志进入 MySQL 或浏览器前必须脱敏。不得展示 Cookie、Token、账号密码、Webhook、完整内部 URL、完整请求/响应正文或 Prefect 管理入口。
- Webhook 写入失败、对账失败和页面断开不能阻塞业务 Flow。服务端记录受控错误类别，页面继续展示最后成功快照。
- `scripts/run.ps1` 当前没有托管独立监控前端端口。`monitor.html` 可从已启动的 Vite 服务访问；正式暴露该入口前，应把进程登记、端口、反向代理与访问控制一并纳入运行方案。

## 验收边界

代码回归应覆盖身份过滤、状态映射、事件去重与乱序、MySQL 投影、Webhook 认证、提交后广播、REST 对账、详情降级、分页、时间线和抽屉键盘行为。

真实环境还必须配置 Prefect Automation，并至少记录 20 次通报状态从 Prefect 发生到页面可见的耗时。P95 不超过 5 秒才可宣称达到当前实时目标；仅通过单元测试、浏览器静态检查或 WebSocket 已连接都不能证明这一项。
