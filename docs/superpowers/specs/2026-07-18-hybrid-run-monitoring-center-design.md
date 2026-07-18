# 混合运行监控中心设计

## 决策

保留 Prefect 作为项目唯一的任务编排、调度、重试和技术运行记录系统；在现有 React 与 FastAPI 应用中新增“运行监控中心”，作为业务人员与技术人员查看运行状态的统一入口。

不引入 Kestra，不迁移既有 Prefect Flow、Deployment、Work Pool 或 Windows Worker。监控中心借鉴 Kestra 的业务仪表盘与执行详情层次，但不复制或替代 Prefect UI。

## 已确认的范围

- 同时覆盖 Prefect Flow（通报、Session Keeper、驾驶舱等）和网页后台操作（保存、发布、安全测试、真实试跑、运行时清理等）。
- 页面通过 WebSocket 实时更新任务状态、步骤、日志和异常。
- 同时服务业务人员与技术人员：业务人员看摘要，技术人员看脱敏后的技术详情并可跳转 Prefect 原始运行。
- 第一阶段只在页面显示异常，不发送企业微信或 Gotify 告警。
- 仅保留最近 7 天的监控历史。
- 使用现有驾驶舱 MySQL，不采用 SQLite，也不直接向 Prefect PostgreSQL 写入自有业务表。

## 总体架构

```text
Prefect Flow / Task ─┐
                     ├─ 监控事件服务 ── MySQL 监控表（7 天）
网页后台操作 ─────────┘        │
                               └─ WebSocket ── 运行监控中心
                                              ├─ 业务概览
                                              └─ 技术详情 → Prefect 原始运行
```

Prefect 继续负责 Flow 与 Task 的状态、日志、重试和调度。监控事件服务只将必要的状态、业务阶段、脱敏日志和异常摘要规范化为统一事件，并在 MySQL 中保存供页面查询。Prefect 的原生记录仍是技术执行的权威来源。

## 组件边界

- `backend/services/monitor_event_service.py`：创建运行、追加步骤、日志和异常；执行脱敏、持久化、广播与保留清理。
- `backend/services/prefect_monitor_adapter.py`：读取 Prefect Flow Run 状态与日志，并转换为监控事件；保留 `prefect_flow_run_id` 和 Prefect UI 跳转地址。
- `backend/routers/monitor.py`：提供概览、运行列表、运行详情 REST 接口与 WebSocket 端点。
- 前端 `monitor/` 页面：业务概览、运行列表、运行详情、实时日志与异常展示。
- 现有 `backend.services.run_log_store`：过渡期可继续写兼容日志文件；新的页面数据以监控事件服务为准，不再依赖每 3 秒轮询 `web_runs.jsonl`。

业务服务和 Flow 不直接操作数据库。它们只调用监控事件服务的清晰接口，例如“运行开始”“阶段更新”“追加日志”“运行成功/失败”。

## 数据模型

在现有 `dashboard_dev` / `dashboard_prod` MySQL 数据库中，通过 Alembic 新增以下项目自有表：

- `monitor_runs`：一次运行的主记录。字段包括 `id`、`source`（`prefect` 或 `web`）、`task_name`、`status`、`started_at`、`finished_at`、`current_step`、`prefect_flow_run_id`、`prefect_ui_url`、`business_error_summary` 与 `technical_error_summary`。
- `monitor_steps`：运行的业务阶段。字段包括 `run_id`、`name`、`sequence`、`status`、`started_at`、`finished_at`、`message`。
- `monitor_events`：按时间追加的日志、状态变化和异常。字段包括 `id`、全局单调递增的 `stream_sequence`、`run_id`、运行内 `run_sequence`、`event_type`、`level`、`message`、`details` 与 `created_at`。

`monitor_events.stream_sequence` 用于 WebSocket 断线重连后的全局增量补发；`run_sequence` 仅用于运行详情内的稳定排序。为运行列表、时间范围、状态、全局流序号和运行内事件查询建立索引。

监控表属于应用数据，而不是驾驶舱指标模型的一部分；通过 `monitor_` 前缀、独立迁移版本和独立服务边界避免与现有驾驶舱 V2 表混用。

## 统一状态和事件

所有来源转换为统一的事件类型：

- `run.started`
- `step.updated`
- `log.appended`
- `run.retrying`
- `run.succeeded`
- `run.failed`
- `run.cancelled`

运行状态对外统一为 `scheduled`、`running`、`succeeded`、`failed`、`retrying` 与 `cancelled`。Prefect 的具体状态保留在技术详情中，但业务人员不需要理解 `AwaitingConcurrencySlot`、`Crashed` 等内部状态。

异常必须带有分类：业务、会话/认证、外部依赖或系统。业务视图显示可处理的摘要；技术视图显示异常类型、失败阶段、脱敏上下文和关联日志。

## Prefect 接入

Prefect 适配器接入以下信息：

1. Flow Run 的创建、运行、重试、完成、失败和取消状态。
2. Flow / Task 的日志及失败信息。
3. 关键业务阶段：会话探活、下载报表、数据比对、生成截图、发送通知和驾驶舱采集等。

Flow 内仍使用 `get_run_logger()` 记录 Prefect 原生日志。监控事件仅补充对业务可读的阶段与摘要，不复制全部调度逻辑。每一条 Prefect 来源的监控运行都必须可跳转到对应 Prefect Flow Run。

## 网页后台操作接入

保存配置、发布、验证、安全测试、真实试跑和运行时清理在开始时创建 `source=web` 的监控运行，在各阶段追加事件，并以成功或失败结束。

现有 `append_log()` 调整为由监控事件服务驱动：可兼容写入 `web_runs.jsonl`，但前端监控中心和后续功能只读取 MySQL 监控记录。这样避免网页操作日志与 Prefect 日志形成两套互不关联的视图。

## API 与实时更新

REST 接口负责首屏和历史查询：

- `GET /api/monitor/summary`：今日成功、运行中、失败、待执行等概览。
- `GET /api/monitor/runs`：按来源、状态、任务和时间筛选最近 7 天运行。
- `GET /api/monitor/runs/{run_id}`：运行详情、步骤、异常和事件。

`WS /api/monitor/stream` 负责实时增量。服务端至少推送 `run.upsert`、`step.updated`、`log.appended`、`error.reported` 和 `run.finished`。

页面打开时先获取 REST 快照，再建立 WebSocket。断线重连时携带已收到的事件序号；服务端补发缺失事件，补发失败或数据过期时前端重新拉取快照。第一阶段以单个 FastAPI 实例为部署前提；若未来扩展为多实例，再引入共享消息总线。

## 页面设计

### 业务概览

- 今日成功、运行中、失败、待执行数量。
- 正在运行任务与当前业务步骤，例如“下载报表（2/4）”。
- 最近失败任务、失败时间和业务错误摘要。
- 最近运行列表：任务名称、来源、状态、开始时间、耗时和结果。

### 运行详情

- 时间线：开始、阶段变更、重试、结束。
- 日志流：按级别筛选、自动滚动、实时追加。
- 异常卡片：失败阶段、分类、业务摘要和技术摘要。
- Prefect 来源显示“打开 Prefect 原始运行”链接。

业务视图默认不显示 Python 堆栈、请求响应或运行秘密。若后续需要真正的角色权限，必须另行引入认证与授权；本期只实现信息分层，不把监控页面暴露到不可信网络。

## 安全和保留策略

- 监控事件进入 MySQL 与 WebSocket 前必须脱敏：禁止记录密码、Cookie、Token、Webhook、完整内部 URL 和完整响应正文。
- 每日清理 `finished_at` 超过 7 天的已结束运行，以及关联步骤和事件；运行中的记录不得清理。长时间运行的任务会在结束后继续保留 7 天。
- Prefect 原始日志继续由 Prefect 的既有保留策略管理；监控中心只保存面向页面的 7 天副本和摘要。
- 失败写入、WebSocket 断开和 Prefect 暂时不可用不得阻断业务 Flow 主流程。监控写入失败应降级记录应用日志，并在服务恢复后继续接收新事件。

## 实施顺序

1. 新增 MySQL 迁移、监控事件服务、保留清理和 REST 查询。
2. 新增 WebSocket 广播、重连补发与服务级测试。
3. 接入网页后台操作，替换日志抽屉的轮询数据来源。
4. 接入 Prefect Flow 状态、日志与业务阶段。
5. 新增业务概览、运行详情、异常展示和 Prefect 跳转。
6. 执行集成验收和安全脱敏检查。

## 验收标准

- Prefect Flow 与网页后台操作均能出现在同一监控中心。
- 页面可实时收到状态、步骤、日志和异常更新。
- 业务人员无需进入 Prefect 即可判断任务是否完成和失败原因。
- 技术人员可从详情跳转到关联的 Prefect 原始运行。
- MySQL 监控记录仅保留最近 7 天，清理不影响运行中的任务。
- 日志、异常、接口响应和 WebSocket 消息不包含认证秘密。
- 不改变现有 Prefect Deployment、Work Pool、Windows Edge 会话、Excel 自动化和企业微信发送逻辑。

## 非目标

- 不使用 Kestra 替代 Prefect。
- 不直接修改 Prefect PostgreSQL 内部表。
- 第一阶段不发送异常告警，不实现长期归档、跨机器日志平台或多实例消息总线。
- 第一阶段不实现用户登录、角色授权或向公网开放技术详情。
