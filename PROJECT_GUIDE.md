# 项目建设与维护指南

此文件是可复用的项目建设流程。新建项目时复制本文件，并在每次需求、架构、配置、依赖或部署变更后更新“变更记录”。

## 使用规则

1. 开始实现前，先补全本文件中的项目目标、运行边界和环境约定。
2. 密码、Token、Cookie、证书和生产地址只存放在被 Git 忽略的本地配置文件，不写入本文档、源码或提交记录。
3. 每次修改完成后，更新本文档的“变更记录”，写明日期、原因、涉及文件、验证命令和结果。
4. 变更依赖、数据库结构、运行入口或部署方式时，必须同步更新对应章节和 README。

## 一、新项目流程

### 1. 定义目标与边界

- 明确用户、核心场景、输入、输出和成功标准。
- 区分必须交付的功能与后续可选能力。
- 明确哪些操作会产生外部副作用，例如发送通知、写数据库、调用生产接口。

### 2. 设计运行架构

- 定义前端、API、任务调度、数据库、文件存储和外部系统的职责。
- 一个功能只保留一个主入口，避免同类脚本、旧 UI 和重复部署方式长期并存。
- 调度系统只负责任务编排；业务配置、数据库配置和本地运行配置分别管理。

### 3. 定义开发环境

- 固定 Python、Node.js、数据库和操作系统版本。
- 将直接依赖写入 `pyproject.toml` 或 `package.json`。
- 使用锁文件保存完整传递依赖，保证不同机器安装一致。
- 约定唯一安装、启动、停止、状态检查和测试命令。

### 4. 管理本地配置与密钥

- 提供可提交的 JSON 配置模板与被 Git 忽略的 `config/runtime.local.json` 实际配置文件。
- 统一本地配置入口，避免数据库、端口和凭据分散在多个脚本或 PowerShell profile。
- 配置文件中只保存连接信息和运行参数，不保存业务代码或临时运行产物。

### 5. 实现与测试

- 先写能描述需求的测试，再写最小实现。
- 每项变更至少运行关联单元测试；涉及启动、构建或部署时运行对应验证。
- 将测试临时目录和运行产物放入项目可写、已忽略的目录。

### 6. 文档与交付

- README 面向使用者：安装、配置、启动、常见操作和故障定位。
- 本文件面向维护者：架构约定、环境标准、变更记录和长期治理规则。
- 数据库迁移、接口契约和高风险运行操作必须有独立说明。

## 二、依赖文件职责

| 文件 | 职责 |
| --- | --- |
| `pyproject.toml` | Python 直接运行依赖与开发工具的唯一人工维护来源。 |
| `requirements.txt` | 兼容 `pip install -r` 的运行依赖入口。 |
| `requirements.lock` | Python 运行时的完整精确依赖树。 |
| `requirements-dev.lock` | 运行依赖加测试、检查和锁文件生成工具的完整依赖树。 |
| `package.json` | 前端直接依赖、开发依赖和 npm 脚本。 |
| `package-lock.json` | npm 的精确依赖树，保证前端安装一致。 |

锁文件通常比直接依赖清单大得多，因为它们同时保存所有传递依赖及其精确版本。

## 三、当前项目标准

### 技术栈

- Python：Conda `base`，Python 3.13。
- 前端：NVM 管理 Node.js 20 LTS，React、Vite、TypeScript。
- 任务调度：Prefect 3.7。
- API：FastAPI 0.115 系列、Starlette 0.46 系列、Uvicorn。
- 数据库：Prefect PostgreSQL 使用 `prefect_prod`（云电脑/生产）和 `prefect_dev`（本机开发）；驾驶舱 MySQL V2 使用 `dashboard_prod` 与 `dashboard_dev`。旧 `prefect_test`、`dashboard_v2` 仅保留作迁移审计源库。
- 本机组件：Microsoft Edge 用于自动登录与采集；Microsoft Excel 用于 COM 比对、模板更新和截图。

### 统一运行配置

私密连接信息统一放在：

```text
config/runtime.local.json
```

该文件被 Git 忽略，可提交模板为：

```text
config/runtime.local.example.json
```

其中按固定顺序维护运行目录与 Pool、Prefect PostgreSQL、驾驶舱 MySQL、监控密钥、模块凭据和任务差异。`prefect.postgres.url` 直接指定 Prefect 使用的数据库，不从 `/prefect` 派生其他库名；驾驶舱实际连接也只读取该本地运行配置。驾驶舱锁覆盖写在 `dashboard.session_overrides`，模块凭据写在 `module_overrides.<模块文件名>`，任务差异写在 `task_overrides.<任务文件名>`。驾驶舱配置 `config/dashboard/session.json` 固定使用 `schema_version: 2`；配置值不应写入 README、本文件或 Git 提交。

| 所在环境 | Prefect PostgreSQL | 驾驶舱 MySQL | 约定 |
| --- | --- | --- | --- |
| 云电脑 / 生产 | `prefect_prod` | `dashboard_prod` | 仅由云电脑的被忽略运行配置连接，用于正式调度与服务。 |
| 本机开发 | `prefect_dev` | `dashboard_dev` | 仅由本机的被忽略运行配置连接，用于开发和验收。 |
| 保留源库 | `prefect_test` | `dashboard_v2` | 不再接入新运行栈；保留至切换验收和回滚窗口结束。 |

Prefect 生产与开发库使用同一份全量快照完成初始化，包含 Deployment、Work Pool、运行记录和日志；两个环境开始运行后允许状态自然分叉，禁止双向同步或从开发库回灌生产库。MySQL 只迁移组织树、指标、公式和目标方案等配置，不迁移采集运行和指标历史；这些数据由各环境独立生成。

Windows 运行时固定使用 `C:\AutoNotifyRuntime`，避免代码升级、分支或 Worktree 切换产生多套 Cookie、Edge Profile、锁和进程注册。运行根目录解析优先级为环境变量 `AUTO_NOTIFY_RUNTIME_ROOT`、`config/runtime.local.json` 中的 `runtime.root`、默认值 `C:\AutoNotifyRuntime`；生产环境应让后两者保持一致，环境变量只用于测试或受控诊断。Prefect 拓扑固定为 `windows-session-pool`、`windows-dashboard-pool`、`windows-notify-pool`，并发上限分别为 `1 / 4 / 6`；`prefect.yaml`、Deployment 和 `runtime.work_pools` 必须保持一致。

运行时目录固定为 `C:\AutoNotifyRuntime`。业务逻辑只允许使用相对运行根目录的受控路径：`session/...`（Cookie、会话健康状态、Edge Profile、锁与登录调试）、`config/{drafts,versions}/...`（未发布草稿与正式配置历史）、`{logs,health,starter_templates,temp}/...`（运行日志、健康探针、新手模板中间产物和工具临时文件）、`modules/<模块名>/output/...`（模块独立产物）和 `flow/<任务名>/{output,backup,debug,tmp}/...`（Flow 产物）。它们分别映射到共享目录的同名顶级目录。运行栈另外维护 `prefect/prefect_home`（本机 Prefect Home）和 `processes`（受管进程登记 JSON）；它们不是业务产物，禁止被 Flow 当作输入/输出目录。

```text
C:\AutoNotifyRuntime\
  session\
    stages\
      report_analysis.json
      city_ops.json
    stage_health.json
    browser-session.json
    browser-profile\
    locks\
      login.lock
      excel_com.lock
      dashboard_collection.lock
    login_debug\
  config\
    drafts\
    versions\<配置名>\
  logs\
    web_runs.jsonl
  health\
  starter_templates\
  modules\<模块名>\output\
  flow\<任务名>\
    output\
    backup\
    debug\
    tmp\
  temp\
  prefect\prefect_home\
  processes\
```

这是一次不兼容目录切换：禁止读取、复制或回退到仓库 `runtime/`、旧的 `cookies`、`browser_session` 或其他未分类路径。业务配置必须使用运行根相对路径并通过统一路径服务映射到共享运行根目录；Flow 内部只可接受受控的运行根相对路径，并继续拒绝绝对路径、`../` 与 `runtime/` 前缀。一次性迁移只在人工执行 `Invoke-RuntimeStateMigration` 时移动 Cookie、会话健康状态、浏览器 Profile、草稿、配置版本、日志、健康检查、新手模板中间产物和登录调试文件；同名目标冲突时保留源目录，不自动覆盖。`setup_windows_env.ps1` 与 `run.ps1` 不得自动迁移旧状态。

旧 `runtime/...` 兼容解析已下线。运行时文件统一通过根相对解析接口处理，禁止再引入“项目相对路径、绝对路径和运行路径混用”的回退逻辑；比较服务中的 `flow/...` 同样必须解析到共享运行根，而项目自有模板等非运行文件继续按项目路径处理。

`setup_windows_env.ps1` 预建 `session\locks`、`config\{drafts,versions}`、`modules`、`flow`、`health`、`logs`、`starter_templates` 和 `temp` 骨架。首次登录由 Session Manager 在 `session\browser-profile` 创建 Profile；Stage SessionBroker 将已验证的阶段数据写入 `session\stages` 并维护 `stage_health.json`。全量 Cookie 快照只可作为 Broker 内部的临时兼容文件，方法结束后必须删除。

`pyproject.toml` 中的 FastAPI 必须保持在 `>=0.110.0,<0.116`。Prefect 3.7 与更高的 FastAPI/Starlette 路由接口不兼容；更新依赖时通过 `requirements.lock` 和 `requirements-dev.lock` 重建并安装精确版本。

### 通报任务配置约定

- `config/task_defaults/notify.json` 保存通报任务的公共步骤、模块配置入口和默认等待参数。
- 已发布的 `config/tasks/<任务名>.json` 默认只保存 `flow_name` 与 `report_config_path`；只有偏离公共默认值的等待或步骤参数才写入覆盖项，禁止重新复制整套默认配置。
- 草稿、安全测试和真实试跑配置写入共享运行目录的 `config/drafts`。API 返回路径时返回运行根相对路径，不得假设文件一定在仓库内。
- 提交、比较、模板更新、会话告警和驾驶舱服务读取运行时文件时必须复用统一运行路径解析，不得各自拼接仓库根目录。驾驶舱失败报告统一写入 `modules/dashboard/output/failure_reports`。

### 驾驶舱数据库约定

- 驾驶舱只保留 V2 数据模型、服务、运行记录与迁移链路，不支持 V1 运行时分派。
- 数据库迁移唯一入口为：`alembic -c alembic_dashboard_v2.ini upgrade head`。
- 运行状态使用 V2 collection-run 存储；状态接口和任务入口不得导入 V1 run store 或 V1 trigger 路径。
- 驾驶舱模块产物写入 `modules/dashboard/output/...`；不保留历史 V2 迁移审核导出工具，历史追溯应使用版本库提交和已归档产物。
- 保留的 V2 配置导入与切换审计工具只接受运行根相对的 `--bundle` 和 `--approvals`，例如 `modules/dashboard/output/v2_migration`；不得传入绝对路径、`runtime/...` 前缀或项目相对替代路径。`--config` 仍是项目内驾驶舱配置路径。
- MySQL 配置迁移完成后，生产与开发库必须分别连接 `dashboard_prod`、`dashboard_dev`；旧 `dashboard_v2` 保持只读留存，禁止让新采集任务继续写入。
- 组织结构读取遇到 MySQL `2006` 或 `2013` 时，只可在废弃当前连接池后以新连接重试一次，固定退避 `0.5` 秒；该规则仅适用于无副作用的读取。诊断日志只能输出固定类别 `MYSQL_CONNECTION_LOST` 或 `MYSQL_READ_TIMEOUT`、错误码、尝试次数、耗时和 `RETRY`/`RECOVERED`/`FAILED` 结果，不得输出原始异常、SQL、连接地址或凭据。
- 实时指标写事务有独立的 `2006`/`2013` 恢复协议：事务异常退出后，必须确认 collection MySQL 命名锁仍由当前任务持有，废弃写入连接池，并用新连接读取同一 `batch_no` 的 `collection_run`。仅当状态已为 `SUCCESS` 时，才可返回已持久化的统计值并停止，不得重放 snapshot；这表示 snapshot 插入、`metric_current` upsert、Run 完成状态和统计值已作为同一事务提交。未观察到 `SUCCESS` 时，可在 `0.5` 秒后完整重试一次；第二次连接丢失直接失败。不得将该协议扩展为无限重试、部分写入重放或绕过命名锁。
- `metric_current` 的 snapshot 决策读取只投影 `node_id`、`indicator_id`、`metric_value`、`stat_date`，不使用整批 `FOR UPDATE`。该无锁读取依赖所有修改当前指标值的写入方使用同一环境的 collection MySQL 命名锁；新增写入路径必须遵守该约束。实时写入顺序固定为：规划 snapshot、插入 snapshot、upsert 全部当前值、将 Run 标记为 `SUCCESS`、提交同一事务。

### 目标草稿、考核版本与历史口径约定

驾驶舱目标采用双轨模型，`TargetPlan` 和 `MetricTargetValue` 的修改必须满足以下约束：

- `DRAFT` 是唯一可编辑状态，也是唯一允许设置 `is_realtime=true` 的状态。实时目标以 `(scenario, period_type)` 为隔离范围；设置一个草稿为实时目标时，服务必须在同一事务内锁定并清除该范围其他草稿的实时标记。保存、导入或创建其他草稿不得隐式切换实时口径。
- `ACTIVE` 与 `RETIRED` 都是只读考核版本。发布不是把草稿原地改为 `ACTIVE`，而是复制草稿和值生成独立记录；源草稿继续保留为可编辑的执行方案。所有写目标值的服务、模板导入、后台脚本和未来 API 都必须拒绝修改已发布记录。
- `ACTIVE` 表示当前尚未被替代的已发布记录，`RETIRED` 表示已被后续考核版本替代但仍必须保留。考核解析必须同时查询这两种状态，不能只过滤 `ACTIVE`；`RETIRED` 是历史和审计链的一部分，不是可删除垃圾数据。
- 版本号在 `(plan_name, scenario, period_type, status)` 范围内有序。草稿 `v1` 与其发布副本 `v1` 可以并存，这是 `20260729_0005` 的数据约束；审计和 API 必须使用记录 ID、状态与谱系字段区分二者，不能把裸 `version_no` 当成全局 ID。
- 前端目标管理只把 `DRAFT` 放入“目标草稿”编辑界面，只把 `ACTIVE`/`RETIRED` 放入“版本记录”只读界面；不在驾驶舱顶部增加“基于某考核版本调整”等轻量状态提示。版本记录必须保留方案名称、场景、周期、版本、使用区间、发布时间、状态、被替代关系和目标值明细，并提供“以此版本创建草稿”。

查询口径固定如下：

| 查询 | 目标来源 | 目标周期 |
| --- | --- | --- |
| 当前实时值、当前实时变化、实时矩阵和下钻 | 命中的 `DRAFT + is_realtime` | `DAY` |
| 实时累计 | 命中的 `DRAFT + is_realtime` | `MONTH` |
| 历史值、历史矩阵和历史下钻 | 按历史业务日期命中的 `ACTIVE/RETIRED` | `DAY` |
| 累计日期查询 `/api/dashboard/acc` | 按累计 `stat_date` 命中的 `ACTIVE/RETIRED` | `DAY` |

实时读取找不到命中的实时草稿时，为兼容旧安装可回退到考核版本。该回退只能作为升级过渡，不能代替显式选择实时草稿。运维或前端不得把未来生效或没有目标值的草稿设为实时口径；否则原实时草稿会被取消，当前日期可能回退到考核版本或显示为空目标。发布层已经拒绝空草稿；若要把“非空草稿”变成强制系统约束，应同时在 `set_target_plan_realtime()`、前端按钮和相关测试中实现，不能只靠文档约定。

`effective_from`/`effective_to` 表示包含两端的业务使用区间，不是发布时刻。解析候选考核版本时按优先级、较晚生效日期、较高版本号和记录 ID 取胜。发布新版本时，服务负责截断与新版本相交的较早时间线，并将其标为 `RETIRED`；同一生效日期的修订保留旧版本审计记录，较高版本号的修订在查询时胜出。业务上应避免不必要的重叠方案，优先使用连续、不重叠的日期区间；优先级只能表达有明确审批依据的覆盖规则。

指标历史事实与考核目标版本的不可变边界必须写清：`metric_snapshot`、`metric_acc` 和采集 Run 不会因发布目标而更新；历史完成率则在查询时按业务日期解析考核版本，当前没有在指标事实表中持久化 `assessment_plan_id`。因此，未来生效的新版本不会改变此前日期的完成率，但“同一生效日”的修订会用新版本重新计算该日期范围的完成率。若业务要求结算结果永久绑定采集当时的考核版本，必须设计迁移：在结算/快照写入时固化考核版本 ID，并让历史查询优先读该绑定；在完成该迁移前，禁止声称当前实现提供采集时版本冻结。

目标管理的唯一应用服务边界为 `services/dashboard_v2_target_service.py` 和 `services/dashboard_v2_target_admin_service.py`，HTTP 边界为 `/api/dashboard/target-plans`、`/target-values` 和 `/target-template`。数据库迁移只能通过 `python -m alembic -c alembic_dashboard_v2.ini upgrade head` 执行；新迁移如影响目标状态、解析顺序或值复制，必须同时补充以下测试：草稿可编辑、发布副本不可编辑、实时草稿独占、保存非实时草稿不切换口径、历史按日期解析、同日修订的版本优先级、空草稿拒绝发布，以及前端文案和 API 契约。

### 运行监控中心约定

运行监控中心只投影 `auto-notify-flow` 下名称以 `notify-` 开头的 Deployment。Prefect 是运行事实来源；FastAPI 接收 Prefect Automation 的状态事件，写入驾驶舱 MySQL 持久化投影，再向单进程内的 WebSocket 客户端广播。后台每 300 秒通过 Prefect 官方 REST API 对账补漏。`5175` 只提供页面，接收事件的服务始终是 FastAPI `8000`。

每个环境均有独立的 `monitor.prefect_webhook_secret`。启动脚本将它导出给后端，Prefect 中的 `monitor-realtime-events` Webhook Block 必须使用同一个环境的值；不得复制其他环境的运行配置或 Block 内容，不得将密钥、请求头值或带敏感参数的回调地址写入仓库、文档、日志、截图或交付物。

每个环境只保留一条已启用的通报状态 Automation。它仅监听上述 Flow 与 Deployment 的 Scheduled、Running、Completed、Failed、Crashed、Cancelled 事件，并调用 `monitor-realtime-events` Block。Session Keeper、驾驶舱采集和其他 Flow 不得匹配。Block 使用 `POST`、允许私有地址，并向 Prefect Server 实际可达的 FastAPI 接收端发送 JSON；不能将页面端口作为回调目标。

`GET /api/live` 的 `monitorEvents` 是运行状态契约：`lastAcceptedAt` 仅表示最近一次被接受的实时事件，`lastReconciledAt` 仅表示最近一次成功 REST 对账，`lastErrorCategory` 表示当前未恢复的上游错误。初次启动的两个时间可以为 `null`；一次成功对账必须刷新 `lastReconciledAt` 并清除先前的对账错误。Prefect Server 重启、FastAPI 生命周期重建或上游短暂不可用时可能出现瞬态 `RECONCILIATION_FAILED`，恢复后必须以随后的成功周期验证清除，不能只依赖页面在线或历史 `202`。

首次部署或变更 Automation 后，须进行端到端验收：验证 Scheduled、Running、Completed 及一次 Failed 或 Crashed，确认 Automation 调用返回 `202`、`lastAcceptedAt` 更新、状态正确投影，且 Scheduled 在 Running 后离开待执行队列；随后等待一个对账周期，确认 `lastReconciledAt` 更新且无错误类别。应记录实际测得的状态变化到页面可见延迟；未完成足够样本的延迟测量前，不得宣称实时 SLA。

### 会话生命周期约定

Session Keeper、业务 Flow、配置式下载和受支持维护工具必须通过 `StageSessionBroker` 获取会话。Broker 使用独立状态锁与全局登录锁，按所需 stage 重建临时兼容快照、探活并持久化独立 stage 快照及健康状态；成功后删除遗留 `cookie_dump.json` 和临时快照。调用方只能消费返回的内存 `stage_data`，不得重新读取 Cookie 文件。

当前登录使用无头模式并保留同一浏览器 Profile 与进程；业务请求明确认证失效时只允许强刷新一次并重试失败步骤一次。Session Keeper 每 10 分钟在同一个浏览器 Profile 中预热 `report_analysis`、`smart_ops`、`city_ops` 与 `data_market`，不发送会话失败或恢复通知。development 环境的最终业务 Run 失败由外层 Flow 按工作负载、业务标识和 Flow Run 去重后发送一次企业微信告警。

专用自动登录 Profile 固定映射为 `C:\AutoNotifyRuntime\session\browser-profile`，不得与日常 Edge Profile 混用，也不得通过全局结束 `msedge.exe` 来清理。完整登录前，持有 `session\locks\login.lock` 的 Session Manager 是唯一允许关闭专用 Edge 的所有者；它必须按进程名和命令行中的专用 Profile 路径识别 `msedge.exe` / `msedgedriver.exe`，不能只依据 `browser-session.json` 中的历史 PID。只有 `taskkill` 退出码为 `0` 才能记为关闭成功。

浏览器关闭遵循条件等待协议：首次关闭后每 `0.5` 秒重新查询专用 Profile 关联进程；进程一旦全部退出即刻继续，不执行无条件睡眠。`browser_close_wait_seconds` 是单轮关闭的总上限，默认 `10` 秒。确认无关联进程后才可启动新的 WebDriver；启动期间如仅出现 Profile 已占用、`DevToolsActivePort` 等释放窗口错误，可在同一动态等待窗口继续尝试。登录子进程由 Manager 在确认清理完成后启动，并通过内部环境标记跳过第二次关闭；直接运行登录脚本时仍需自行执行相同的定向清理。

进程查询不可用、目标进程仍存在或关闭失败时，状态必须为 `browser_cleanup_failed`：保留 `session\browser-session.json`，不得创建新的 Edge、不得删除 Profile、不得写入新的 Cookie，也不得把进程查询失败伪装为“没有残留”。此分类完成动态等待后直接结束本轮，不进行第二次完整登录。Session Keeper 的其他登录失败最多执行第二次完整登录；两次之间不睡眠固定 `60` 秒，下一次尝试开始前仍由定向 Profile 清理按条件轮询决定何时可启动。Worker 账户必须有权执行 `Get-CimInstance Win32_Process` 查询本用户进程；权限缺失时应修复 Windows/WMI 权限，不得用结束所有 Edge 进程作为替代方案。

登录子进程失败时，允许跨进程传递且写入日志的诊断仅为 `AUTO_NOTIFY_LOGIN_DIAGNOSTIC` 中受控的阶段名、Python 异常类名和白名单原因码。阶段只能是 `init_driver`、`ngboss_login`、`ngboss_main`、`app_login`、`usm_console`、`app_session_capture` 或 `session_validation`；原因只能是 Profile 占用、DevTools 端口、浏览器启动、驱动失联、未分类 WebDriver、超时或未知异常的稳定代码。不得记录或解析子进程原始 stderr/stdout、登录命令、认证材料、完整 URL、查询参数或 Selenium 原始异常文本。

旧 Worker 与遗留专用 Edge 分开处理：普通启动发现已登记 Worker 仍在运行时必须失败关闭，运维人员先执行 `scripts/stop.ps1`；Worker 已停止但仅遗留专用 Edge 时，由下一次完整登录在全局锁内完成定向清理。`scripts/stop.ps1` 不承担任意浏览器进程的清理职责，避免干扰非本项目的用户浏览器。

每次 Session Keeper 成功完成预热后，Flow 日志必须按返回的阶段列表记录共享会话健康确认；阶段列表为空时不记录该确认信息。

`autologin.json` 中 `stage_probes.<stage>` 默认配置单个探活对象；需要更严格的鉴权校验时可改用 `probes` 数组。所有启用探活均成功才判定该 stage 健康；探活的 Cookie、Token 和 Storage 值必须从当前 stage 快照动态注入，禁止在配置、日志或文档中写入固定认证材料。

`city_ops` 探活固定使用连接超时 `2` 秒和读取超时 `5` 秒。只有 `requests` 网络异常可在固定退避 `0.5` 秒后重试一次，总共最多两次；一旦获得 HTTP 响应即不重试，认证响应继续按既有会话刷新边界处理。两次网络异常后仅允许记录异常类别，如 `ConnectTimeout` 或 `ReadTimeout`，不得记录原始异常文本、请求头、Cookie、Token 或请求体。其他 stage 未设置分离超时时保持 `timeout_seconds` 的既有单值语义。

请求故障按认证失效、基础设施、接口契约和业务结果处理。302、401、403、登录页语义和 `reCode=1101` 的认证边界，以及 429、5xx、超时和 JSON 契约失败的处理规则，统一见 [docs/request-failure-handling.md](docs/request-failure-handling.md)。运行日志不得输出完整内部 URL、认证材料或响应正文。

### 运行入口

环境初始化、启动、状态和停止入口为：

```powershell
pwsh -File scripts/setup_windows_env.ps1
pwsh -File scripts/run.ps1
pwsh -File scripts/status.ps1
pwsh -File scripts/stop.ps1
```

系统只允许专用 Windows 用户在保持登录和交互式桌面会话时手工启动，不配置开机自启。主机重启、用户重新登录或 Prefect Server 停止后，运维人员必须再次执行 `scripts/run.ps1`。旧 Worker 预检通过并启动 Prefect Server 后，脚本会在启动任何新 Worker 前取消本项目全部已过期的排队 `SCHEDULED` / `PENDING` Run；随后启动并确认 Session Worker 在线，唯一一次提交新的 Session Keeper Run，最后启动 Dashboard 和 Notify Worker。提交不会等待 Session Keeper Flow 完成。预计开始时间仍在未来、缺少预计开始时间的排队 Run，以及不属于本项目受管 Deployment 的 Run，会原样保留且不受此清理影响。脚本启动一个 Server、三个 Worker、FastAPI，以及三个独立的 Vite 前端服务：配置中心 `5173`、数据驾驶舱 `5174` 和运行监控中心 `5175`，应用 Pool 上限 `1 / 4 / 6`；它不发布、删除或同步仓库中的 Deployment 定义，但会在清理期间临时暂停并恢复原本未暂停的受管 Deployment，并将 Notify Deployment 的 Work Pool 校正为运行配置指定的 Pool。通报与驾驶舱采集仍由既有 Prefect Deployment 的 Cron 执行，不会自动触发全部通报。

普通启动会在启动 Prefect Server 前检查进程登记中的 Session、Dashboard 和 Notify Worker，避免旧 Worker 在启动清理前领取排队任务。任一已登记 Worker 仍在运行时必须失败关闭；运维人员应先执行 `scripts/stop.ps1`，确认旧进程停止后再正常运行 `scripts/run.ps1`。`-ForceRestart` 同时会取消运行中的本项目 Run，不能作为绕过该检查的常规手段。

统一 `session-keeper` 发布并确认可运行后，必须在 Prefect UI 或命令行一次性删除历史 `session-keeper-flow/session-keeper-report` 与 `session-keeper-flow/session-keeper-city` Deployment；从 `prefect.yaml` 删除声明不会清理 Prefect 服务端已有的调度对象。不得由 `scripts/run.ps1` 自动删除 Deployment。

每次启动都会取消本项目全部已过期的排队 `SCHEDULED` / `PENDING` Run，不区分自动或手工触发；预计开始时间在未来或缺失的排队 Run 会保留，也不补跑 Dashboard 等历史批次。处于 `RUNNING`、`CANCELLING` 或 `PAUSED` 的本项目 Run 会阻止替代 Worker 启动；只有 `scripts/run.ps1 -ForceRestart` 才会取消这些运行中的 Run。Worker 监督进程在崩溃 30 秒后重启 Worker；Prefect Server 需要手工执行 `scripts/run.ps1` 恢复。`scripts/stop.ps1` 只能停止 `C:\AutoNotifyRuntime\processes` 中已登记且身份匹配的进程。

`scripts/status.ps1` 从 Prefect API 读取配置中的 Pool 名称和实际并发上限，并列出排队超过 10 分钟的自动调度 Run。锁协议当前不记录等待者，状态输出必须明确显示 `waiters=unavailable`，不得声称能展示等待数量。

### 前端端口与云电脑 FRP

- 配置中心服务固定监听 `127.0.0.1:5173`，入口为 `http://127.0.0.1:5173/`。
- 数据驾驶舱服务固定监听 `127.0.0.1:5174`，其根路径会映射到驾驶舱页面，入口为 `http://127.0.0.1:5174/`；不得要求用户输入 `dashboard.html`。
- 为避免入口混用，配置中心的 `/dashboard.html` 和驾驶舱的 `/index.html` 必须返回 `404`。这只隔离 Web 页面入口，不替代 API 鉴权。
- 云电脑使用 FRP 时，配置中心应采用 `5173 -> 15173`，数据驾驶舱采用 `5174 -> 15176`；外部访问地址分别为 `http://服务器地址:15173/` 与 `http://服务器地址:15176/`。

旧 Notify Pool 上若仍有预计开始时间未到或缺失的保留 Run，启动必须列出 Run 身份并失败关闭。Prefect 3.7 无法安全改派单个已排队 Run，也不能为共享旧 Pool 自动启动不受 Run ID 约束的 Worker；运维人员应先受控排空或取消列出的旧 Run。由于 Prefect Server 已注册，随后必须执行 `scripts/stop.ps1` 再运行 `scripts/run.ps1`。`scripts/run.ps1 -ForceRestart` 只取消运行中的 Run，不能绕过旧队列的失败关闭。禁止删除 Run 强行完成切换。

所有 Excel COM 阶段通过 `C:\AutoNotifyRuntime\session\locks\excel_com.lock` 串行，登录刷新通过 `C:\AutoNotifyRuntime\session\locks\login.lock` 串行；提高 Notify 并发不得绕过这两个锁。

截图流程启动 Excel 前必须先把 Windows 默认打印机切换为配置的 `Microsoft Print to PDF`，再创建 COM 实例，避免 Excel 继承 RustDesk 等虚拟打印机。打印机配置使用系统打印机名称，不写端口后缀；切换成功后不自动恢复旧默认打印机，因此专用 Windows 用户不应依赖其他默认打印机。

## 四、变更记录

### 2026-07-30 - 驾驶舱目标草稿与考核版本双轨管理

- 原因：实时执行目标需要允许持续调整，而历史完成率、已发布考核依据和审计记录不能与可编辑方案混在同一状态中。
- 修改内容：目标管理界面分为可编辑的“目标草稿”和只读的“考核版本记录”；草稿可显式设为实时目标，发布时复制为 `ACTIVE` 考核版本，原草稿继续可编辑。历史和累计按生效日期解析 `ACTIVE/RETIRED`，同日修订由较高版本号胜出；README 与本指南补充了数据来源、操作顺序、时间线规则、接口、迁移和不可变边界。
- 涉及文件：`models/dashboard_v2.py`、`services/dashboard_v2_target_service.py`、`services/dashboard_v2_target_admin_service.py`、`services/dashboard_v2_query_service.py`、`backend/routers/dashboard.py`、`frontend/src/dashboard/DashboardCockpit.tsx`、`frontend/src/lib/api.ts`、`frontend/src/types/dashboard.ts`、`migrations/dashboard_v2/versions/20260729_0005_target_plan_draft_publish.py`、`README.md`、`PROJECT_GUIDE.md` 及相关测试。
- 配置或迁移：目标库升级 `python -m alembic -c alembic_dashboard_v2.ini upgrade head`。迁移增加 `is_realtime` 和索引，不改写已有目标值、指标快照或累计数据；升级后须由管理员选择一份当前有效的非空草稿作为实时目标。
- 验证：目标相关后端测试 `46 passed`，前端 `npm run typecheck` 通过，`git diff --check` 通过；应在目标环境完成一次草稿保存、实时切换、发布、历史日期查询和同日修订的人工验收。
- 风险与回滚：同日修订会重新计算该日期范围的历史完成率，但不会改写指标事实；若需要采集时版本冻结，必须先实现版本 ID 固化迁移。回滚代码前必须评估已执行的 `20260729_0005`，不能仅删除 `is_realtime` 列或已发布审计记录。

### 2026-07-27 - 云电脑通报实时事件接入验收

- 原因：监控中心此前主要依赖五分钟 REST 对账，无法在 Prefect 通报状态变化后立即更新页面；云电脑需要独立完成上游事件接入，且不得复用本机认证材料。
- 修改内容：在云电脑 Prefect 中配置独立的 `monitor-realtime-events` Webhook Block，并将唯一通报状态 Automation 限定为 `auto-notify-flow` 的 `notify-*` Deployment 与六类 Flow Run 状态。回调事件由 FastAPI 接收后写入监控投影并向页面广播；文档补齐了 Block、Automation、`/api/live` 三个状态字段和对账窗口的运维契约。
- 配置与安全：云电脑使用其本机 `runtime.local.json` 中的独立监控密钥，后端与 Block 使用同一值；不记录或提交密钥、Header 值、Cookie、Token、回调地址参数或环境专属 Automation ID。
- 验证：已验证 Scheduled、Running、Completed 和受控 Failed 状态均由 Automation 返回 `202` 并进入监控中心；`lastAcceptedAt` 更新。Prefect Server 重启窗口曾出现瞬态 `RECONCILIATION_FAILED`，服务恢复后下一次成功对账已更新 `lastReconciledAt` 并清除错误类别。
- 风险与处理：页面 WebSocket、实时 Webhook 和 REST 对账是三条不同信号。任一单项成功都不能证明其余两项正常；若对账错误持续，检查 FastAPI 生命周期、Prefect API、MySQL 和 Automation 过滤条件，恢复后等待并核验一个完整对账周期。

### 2026-07-23 - 驾驶舱实时指标写事务连接恢复与无锁读取

- 原因：整批 `metric_current` 的 `SELECT ... FOR UPDATE` 会对约 38,000 条当前指标请求行锁；MySQL `2013` 后若盲目重试，无法区分未提交事务与提交回执丢失，可能重放 snapshot。
- 修改内容：当前值查询改为四列无锁投影；写事务对 `2006`/`2013` 在确认 collection 命名锁后废弃连接池，并查询本批 `SUCCESS` 状态。已提交时恢复持久化结果，未提交时仅完整重试一次。
- 涉及文件：`services/dashboard_v2_metric_store.py`、`services/dashboard_v2_pipeline.py`、对应测试、`README.md`、`PROJECT_GUIDE.md`。
- 配置或迁移：无需迁移，不调整 Cron、数据库超时或锁等待配置；所有当前指标写入方必须继续使用同一环境的 collection 命名锁。
- 验证：`python -m pytest -p no:cacheprovider -q tests/test_dashboard_v2_metric_store.py tests/test_dashboard_v2_pipeline.py tests/test_dashboard_lock_commit_gates.py` 为 `19 passed`；Ruff 与 `git diff --check` 通过。
- 风险与回滚：发生第二次连接丢失时任务仍会失败，避免无限重试和重复快照。回滚时应同时恢复无锁读取、写事务恢复逻辑及其测试，不得只恢复其中一部分。

### 2026-07-23 - City Ops 探活瞬态网络恢复

- 原因：`city_ops` 探活使用单个 8 秒超时，短暂的网络连接或读取波动会直接中止驾驶舱采集，且日志会截断底层异常的关键类别。
- 修改内容：对该 stage 配置连接超时 2 秒、读取超时 5 秒；仅网络异常固定等待 0.5 秒并重试一次，HTTP 响应不重试。连续两次失败仅记录安全的请求异常类别。
- 涉及文件：`config/modules/autologin.json`、`services/session_manager.py`、`tests/test_session_manager.py`、`README.md`、`PROJECT_GUIDE.md`。
- 配置或迁移：无需迁移；仅更新 `stage_probes.city_ops` 超时字段。
- 验证：`python -m pytest tests/test_session_manager.py -v` 为 69 passed；Ruff、JSON 校验和目标差异检查通过。
- 风险与回滚：持续基础设施故障在两次请求后仍会使当前任务失败，不触发自动登录；回滚该变更可恢复单次 8 秒探活语义。

### 2026-07-22 - 驾驶舱 MySQL 结构读取恢复

- 原因：组织结构读取在结果接收阶段发生 MySQL `2013` 超时时，单次暂态连接中断会直接使整轮驾驶舱采集失败。
- 修改内容：对只读结构加载仅识别 `2006`、`2013`，废弃连接池后固定等待 `0.5` 秒并以新连接重试一次；补充只含错误类别、错误码、尝试次数、耗时和恢复结果的受控日志。写事务重试策略保持不变。
- 涉及文件：`services/dashboard_v2_orchestrator.py`、`tests/test_dashboard_v2_orchestrator.py`、`README.md`、`PROJECT_GUIDE.md`。
- 配置或迁移：无；不调整 MySQL 运行时配置，不新增索引。
- 验证：`python -m pytest -q`，`664 passed, 9 skipped`；`ruff check services/dashboard_v2_orchestrator.py tests/test_dashboard_v2_orchestrator.py` 通过。
- 风险与回滚：连续两次读取失败仍会使当前批次失败，以保留真实基础设施故障；回滚该读取重试提交可恢复单次读取语义。

### 2026-07-19 - 通报待执行队列筛选

- 原因：待执行队列同时展示会话维护、驾驶舱采集和通报计划，无法作为通报排程视图使用。
- 修改内容：`MonitorCenter` 仅从完整 `PendingQueue` 中派生 `targetId` 以 `report-` 开头的计划，并以该派生队列驱动按钮、无障碍标签和抽屉。
- 涉及文件：`frontend/src/monitor/MonitorCenter.tsx`、`frontend/src/monitor/MonitorCenter.test.tsx`、`PROJECT_GUIDE.md`。
- 配置或迁移：无；API 和实时流继续传输完整待执行队列。
- 验证：运行 MonitorCenter 聚焦测试、前端类型检查、生产构建和浏览器交互检查。
- 风险与回滚：筛选依赖 `targetId` 的 `report-` 命名契约；如需恢复完整运维队列，回退 `MonitorCenter` 的派生队列改动即可。

### 2026-07-21 - 统一本地运行 JSON 配置

- 原因：运行信息已集中在 `runtime.local.json`，但模块凭据、驾驶舱锁和任务差异仍分散在同级侧车 JSON，导致每台机器配置路径过多且难以核对。
- 修改内容：`config/runtime.local.json` 成为唯一活跃的私有 JSON 文件，按运行、Prefect、驾驶舱、监控、模块与任务六个分区组织。加载器不再读取侧车文件；一次性迁移工具先预览、冲突失败、原子写入，再按显式参数删除已验证的旧文件。
- 配置与迁移：每台开发机或生产机分别执行 `python scripts/migrate_local_json_to_runtime.py`，确认无冲突后执行 `--apply --remove-legacy`；不得复制、提交或输出任何环境的私有配置。
- 验证：中央覆盖、侧车禁读、迁移预览、冲突、删除和 PowerShell 配置校验均由自动化测试覆盖。

### 2026-07-23 - 运行监控对账异常隔离与摘要限长

- 原因：对账适配器会尝试投影非通报 Flow Run；某次 `dashboard-collection` 的 MySQL 连接异常文本超过 MySQL `TEXT` 容量，导致监控投影写入失败并错误显示 `RECONCILIATION_FAILED`。
- 修改内容：定时对账与 Webhook 统一只处理 `auto-notify-flow` 下名称以 `notify-` 开头的通报 Deployment。业务错误摘要在脱敏后最多保留 500 字符，技术摘要、步骤消息和日志详情最多保留 8,000 字符，并标记已截断；原始技术信息仍由 Prefect 保留。
- 验证：新增非通报对账过滤和 100KB 异常摘要的回归测试；监控服务、适配器、Webhook、MySQL Store、路由和生命周期相关 pytest 分组均通过。

### 2026-07-21 - 通报运行监控中心实时投影与可用性收口

- 原因：早期监控页仍混合 7 天历史、非通报任务和周期性全量同步，页面连接状态也容易被误读为 Prefect 数据正常；失败运行缺少按需可查的真实步骤与日志。
- 修改内容：新增以 MySQL 为持久化投影的通报监控中心。Prefect Automation 通过带密钥的 Webhook 提交 Flow Run 状态，服务端按事件 ID 去重、按发生时间拒绝旧状态覆盖，并在事务提交后仅广播对应 `run.updated` 增量。首屏和重连使用 REST 快照；后台每 5 分钟调用 Prefect 官方 REST API 对账补漏。页面仅保留近 30 天的通报成功、运行中和失败记录；待执行独立为全局通报 `Scheduled` 队列，返回真实 `targetId` 与 `nextStep`。
- 交互与信息层级：运行记录支持 10、20、50 条分页，时间线可按日期收起展开并分批加载。记录详情默认展示业务摘要；点击 Prefect 运行后按需读取 Task Run 和脱敏日志。抽屉支持对话框语义、初始焦点、`Escape` 关闭与 Tab/Shift+Tab 焦点循环。页面分别显示 WebSocket 连接、上游事件时间和最近对账时间。
- 数据与迁移：驾驶舱 MySQL 新增 `monitor_runs`、`monitor_steps`、`monitor_events`，并通过 `20260718_0002`、`20260719_0003`、`20260719_0004` 迁移补齐通报身份、状态发生时间和事件幂等键；已结束通报记录保留 30 天。Prefect PostgreSQL 仍只通过官方 API 读取，禁止直接写入内部表。
- 涉及文件：`backend/routers/monitor.py`、`backend/services/monitor_*`、`backend/services/prefect_monitor_*`、`models/monitor.py`、`migrations/dashboard_v2/versions/20260718_0002_monitor_center.py` 至 `20260719_0004_monitor_event_idempotency.py`、`frontend/monitor.html`、`frontend/src/monitor/`、相关 pytest/Vitest 测试以及运行监控文档。
- 配置与运维：`config/runtime.local.json` 必须配置且仅在本机保存 `monitor.prefect_webhook_secret`；启动配置会导出 `PREFECT_MONITOR_WEBHOOK_SECRET`。还需在 Prefect 中人工创建只匹配 `auto-notify-flow` 和 `notify-` Deployment 的 Automation，具体步骤见 `docs/operations/prefect-monitor-webhook.md`。`scripts/run.ps1` 将运行监控中心作为受托管前端服务启动在 `http://127.0.0.1:5175/`，`/monitor.html` 保持兼容访问。
- 验证与风险：单元、路由、流、同步、迁移与前端回归测试用于覆盖契约；真实 Automation 端到端延迟须在目标环境按至少 20 次状态变化测量，P95 不超过 5 秒才可认定达到实时目标。单 FastAPI 进程以进程内 Hub 广播，扩展为多进程或多实例前必须引入共享消息总线。
### 2026-07-17 - 前端双端口与驾驶舱根路径入口

- 原因：配置中心和数据驾驶舱共享单个 Vite 端口，云电脑的 FRP 无法为两个界面提供独立入口，且驾驶舱 URL 需要暴露 `dashboard.html`。
- 修改内容：启动、状态和停止脚本分别管理配置中心 `5173` 与驾驶舱 `5174`；驾驶舱 Vite 模式将根路径映射到驾驶舱页面，配置中心拒绝 `/dashboard.html`，驾驶舱拒绝 `/index.html`。README 与本指南记录 FRP 的 `15173`、`15176` 映射。
- 配置或迁移：云电脑 FRP 配置应将 `localPort = 5173` 映射到 `remotePort = 15173`，将 `localPort = 5174` 映射到 `remotePort = 15176`；无需数据库迁移。
- 验证：运行前端类型检查、生产构建、开发环境契约测试与完整 Pytest；临时启动两个 Vite 端口，验证配置中心根路径和驾驶舱根路径为 `200`，交叉入口为 `404`。
- 风险与回滚：该隔离仅限制前端入口路径；如需回滚，整体回退前端脚本、Vite 配置、文档与契约测试，恢复单端口入口。

### 2026-07-17 - 生产与开发数据库拓扑及数据迁移对齐

- 原因：原有维护约定仍将 `prefect_test` 与 `dashboard_v2` 描述为当前运行库，无法反映云电脑生产环境与本机开发环境的隔离需求。
- 修改内容：确定 `prefect_prod/dashboard_prod` 为云电脑生产库、`prefect_dev/dashboard_dev` 为本机开发库；旧库转为迁移审计源库。Prefect PostgreSQL 使用同一份全量快照初始化两个目标库；MySQL 仅迁移组织树、指标、公式与目标方案配置。
- 涉及文件：`README.md`、`PROJECT_GUIDE.md`。
- 配置或迁移：各机器只在被 Git 忽略的 `config/runtime.local.json` 中指向所属环境数据库；不得提交凭据、将开发库回灌生产库，或对两个运行库建立双向同步。
- 验证：Prefect 两个目标库均核验 36 张表、72,283 行、迁移版本 `7218aad17905` 和 33 个外键；MySQL 两个目标库均核验基础组织树、指标、公式、目标方案以及无历史指标数据；`git diff --check` 通过。
- 风险与回滚：两个环境一旦运行会自然产生不同的调度与运行记录；回滚应切换回保留源库或已验证快照，不得用任一环境的后续状态覆盖另一环境。

### 2026-07-17 - Prefect 过期 Run 统一清理与首次 Keeper 启动顺序

- 原因：不同 Deployment 过去采用不同的过期处理策略，Notify 还保留十分钟宽限期配置，导致启动后可能执行过期的 Dashboard 补跑或保留无效队列。
- 修改内容：启动清理统一取消本项目所有预计开始时间已过的 `SCHEDULED` / `PENDING` Run，保留未来或缺少预计开始时间的排队 Run，并取消 Dashboard 历史补跑；普通启动会先拒绝仍有已登记旧 Worker 的状态，以保证清理先于任何 Worker 领取任务；Session Worker 在线后仅提交一次新的 Session Keeper Run，再启动 Dashboard 和 Notify Worker，且不等待该 Flow 完成。
- 配置或迁移：删除 Notify 宽限期配置 `AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS`；运行中的 Run 仍仅在 `scripts/run.ps1 -ForceRestart` 时取消。
- 验证：运行启动顺序与启动清理契约测试、完整 pytest 和 `git diff --check`。
- 风险与回滚：普通启动会放弃所有已过期的排队批次；如需保留某个批次，应先调整其预计开始时间或在启动前手工处置。回滚必须同时恢复清理策略、启动顺序和文档，避免新旧运维规则混用。

### 2026-07-15 - 腾讯文档自动读取范围优化

- 原因：腾讯 Sheet 元数据同时包含实际数据边界和工作表容量边界；原逻辑优先使用容量边界，导致自动下载空白单元格，并会将用户指定的显式 A1 范围截断。
- 修改内容：自动范围（空值、`auto`、`used`、`used_range`）优先采用 `rowCount` 与 `columnCount`，缺失时回退至 `rowTotal` 与 `columnTotal`；显式 A1 范围只做语法规范化，按原范围请求。下载元数据请求改为完整模式，以获得实际数据边界；后续分块若超出实际数据区，继续按既有无效范围逻辑停止读取。
- 涉及文件：`services/tencent_sheet_service.py`、`tests/test_tencent_sheet_service.py`、`PROJECT_GUIDE.md`。
- 配置或迁移：无新增配置或依赖。现有任务将范围设为 `auto` 即可使用实际数据边界；需要固定读取区间时使用显式 A1 范围。
- 验证：`python -m pytest tests/test_tencent_sheet_service.py -k resolve_sheet_range -v`；`python -m pytest tests/test_tencent_sheet_service.py -v`；`git diff --check`。
- 风险与回滚：显式范围不再预先裁剪，超出实际数据区时会额外发起分块请求，并在接口返回无效范围后停止；若需恢复旧的容量边界和预裁剪行为，须整体回滚该服务与测试变更。

### 2026-07-16 - 腾讯智能表格导出与前端配置

- 原因：腾讯智能表格的子表、字段和记录接口与普通腾讯 Sheet 不同，不能用 A1 范围读取；此前只能通过一次性诊断脚本导出，无法在通报配置中使用。
- 修改内容：新增 `tencent_smartbook` 数据源及正式导出服务，按配置顺序导出指定子表的全部字段和分页记录，过滤空 `values` 记录并保留抓取/导出计数；选项、人员、链接等富值转换为可读文本。支持 OpenAPI 的 `getSheet`、`getFields`、`getRecords` 响应包装和 `next` 分页游标。下载调度、Flow、配置标准化、新手模板、Prefect 校验及 React“智能表格”第三标签同步支持该来源；诊断脚本改为调用正式服务。
- 涉及文件：`services/tencent_smartbook_service.py`、`services/method_service.py`、`flows/notify_single_flow.py`、`backend/services/{config_store,starter_template,prefect_runner}.py`、`frontend/src/{types/config.ts,schemas/reportConfigSchema.ts,components/config-form/ConfigForm.tsx}`、`scripts/dev/test_tencent_smartbook_export.py`、README 与相关测试。
- 配置或迁移：使用 `source: "tencent_smartbook"`；外层字段沿用腾讯 Sheet 的 `name`、`doc_url`/`file_id`、`output_filename` 与 `sheets`，但子表只使用 `sheet_id` 或 `sheet_name` 和可选 `output_sheet_name`，不得配置 `range`。`sheet_id` 可从链接的 `tab` 参数取得，若同时配置名称则 ID 优先；`sheets` 配置顺序决定输出 Sheet 顺序。凭据只保留在被忽略的 `config/modules/tencent_docs.local.json`，前端不处理凭据。
- 验证：执行 Smartbook、腾讯 Sheet、下载、Flow、配置、新手模板和 Prefect 聚焦测试；运行 Ruff、前端类型检查和生产构建；手工探针只核验工作表名、表头数和行数。
- 风险与回滚：OpenAPI 权限、访问令牌或子表 ID 无效会使该下载项失败，但不会读取业务 Cookie Stage；回滚时须同时移除该数据源的前后端、调度和诊断脚本变更，不能将智能表格配置改作普通 Sheet 范围配置。

### 2026-07-15 - 运行根相对路径收敛与兼容层下线

- 原因：运行目录已迁移至共享 `runtime.root`，但少量配置、运行记录和运维脚本仍保留 `runtime/...` 外部表示或旧解析 API，导致同一输入可能被误解为项目路径，并影响 Session Keeper 预检与业务 Flow 对共享会话状态的复用。
- 修改内容：运行目录的外部表示统一为 `session/...`、`config/...`、`modules/...`、`flow/...` 等受控根相对路径；删除旧 `resolve_runtime_path()` 与 `validate_runtime_path()` 兼容层；比较服务正确解析 `flow/...`，并拒绝历史前缀；Dashboard V2 配置导入和切换审计的 `--bundle`、`--approvals` 统一从共享运行根解析；前端运行文件抽屉不再显示 `runtime/` 前缀。
- 涉及文件：`services/runtime_paths.py`、`services/compare_service.py`、`flows/notify_single_flow.py`、运行目录相关后端服务与配置、`scripts/tools/dashboard/{import_v2_indicator_config,v2_cutover_audit}.py`、`frontend/src/components/runtime/RuntimeDrawer.tsx`、相关测试、README 与运行路径审计文档。
- 配置或迁移：`runtime.root` 的环境变量、本机 JSON 配置和默认根目录优先级不变；所有模块 JSON、任务配置和运维参数必须去除 `runtime/` 前缀。已存在的旧运行状态只可通过人工迁移流程处理，程序不会兼容读取或自动回退。
- 验证：`python -m pytest -p no:cacheprovider -q`，`550 passed, 9 skipped`；`rg -n 'resolve_runtime_path\\(|validate_runtime_path\\(' services scripts tests` 无匹配；`git diff --check` 通过。
- 风险与回滚：这是不兼容收敛。旧脚本、手工命令或配置若继续传递 `runtime/...`、绝对路径或 `..` 会被拒绝；回滚必须整体恢复路径迁移和兼容层删除前的提交，禁止新旧路径契约混用。

### 2026-07-14 - 阶段会话收敛与脚本兼容层清理

- 原因：共享 `cookie_dump.json` 使不同业务 stage 相互影响，下载与维护脚本可绕过会话策略；旧公网栈、PowerShell profile 回退和一次性迁移脚本长期共存，增加凭据残留与误启动风险。
- 修改内容：以 `StageSessionBroker` 统一 Session Keeper、通知 Flow、驾驶舱 V2、新手模板、配置式下载及受支持指标样本工具的会话准备；下载器仅接受内存 `stage_data`；Broker 返回保留底层诊断字段、以独立锁串行化 stage 状态、成功后清理临时及遗留全量 Cookie 快照。业务告警仅在 development 环境最终失败时发送一次，Keeper 改为分阶段预热。删除旧公网栈、兼容转发、City Ops 临时诊断、历史 V2 迁移导出和硬编码模板脚本；启动链只读取 `config/runtime.local.json`，不再加载 PowerShell 本机 profile。
- 涉及文件：`services/{session_broker,session_manager,method_service}.py`、`tasks/session_tasks.py`、`flows/notify_single_flow.py`、`services/dashboard_v2_*`、`backend/services/starter_template.py`、`services/business_run_alert_service.py`、`config/modules/session_keeper*.json`、`scripts/`、`README.md`、`PROJECT_GUIDE.md` 与相关测试。
- 配置或迁移：运行环境必须具备 `config/runtime.local.json`；保留的 `*.local.ps1` 不再被加载。旧公网栈、V1/V2 历史迁移导出和 Cookie 文件回退均不受支持；如需追溯或恢复，只能使用清理前的版本库提交与隔离运行环境。
- 验证：`python -m pytest -p no:cacheprovider tests/test_development_environment_contract.py tests/test_session_broker.py tests/test_session_manager.py tests/test_method_service.py tests/test_notify_flow_session.py tests/test_dashboard_v2_trigger.py tests/test_session_keeper_flow.py tests/test_starter_template.py tests/test_dashboard_metric_sample.py -q`，`158 passed`；Python 编译检查与 `git diff --check` 通过。
- 风险与回滚：运行前必须完成 JSON 本机配置，不能依赖旧 PowerShell profile；回滚必须整体切回清理前提交，不能混用旧脚本、全量 Cookie 快照和新 Broker 状态目录。

### 2026-07-14 - Runtime 路径收口与驾驶舱 V2-only 清理

- 原因：共享运行目录迁移后，新手模板清单和配置删除仍尝试生成仓库相对路径；驾驶舱还保留 V1 任务分派、手工入口、数据库模型、迁移链和旧输出目录。
- 修改内容：增加统一路径展示和运行根目录绝对路径校验；修复草稿删除、新手模板和迁移工具的共享 Runtime 路径；抽离 V2 所需的采集、结构比对、指标和运行记录通用能力；删除 V1 运行代码、迁移、手工工具与测试；任务、手工采集、健康检查和 MySQL 本机覆盖均固定为 V2。
- 涉及文件：`services/runtime_paths.py`、`backend/services/{config_store,starter_template,prefect_runner,health_service}.py`、`services/dashboard_*`、`infrastructure/dashboard_run_protocol.py`、`tasks/dashboard_tasks.py`、`scripts/tools/dashboard/`、`models/dashboard_*.py`、`migrations/dashboard/`、V2 测试与项目文档。
- 配置或迁移：当前运行环境只允许 `dashboard_v2` 和 `alembic_dashboard_v2.ini`；已移除的 V1 Alembic 链不能用于回滚。历史迁移审核应使用版本库提交和已归档产物。
- 验证：运行 Runtime 与 Dashboard V2 聚焦测试、完整 Pytest 收集、Ruff、`compileall`、V1 引用扫描和 `git diff --check`。
- 风险与回滚：该清理不再支持 V1 数据库或脚本回退；如需恢复 V1，必须切回清理前的完整提交并使用独立数据库，禁止与 V2 代码或数据库混用。

### 2026-07-13 - 共享运行路径与 Excel 截图稳定性修复

- 原因：部分服务仍将 `runtime/...` 解释为仓库内目录，草稿和真实试跑配置位于共享运行目录时又被强制转成仓库相对路径；同时 Excel 可能在启动时继承远程控制虚拟打印机，导致截图阶段无法设置稳定打印机。
- 修改内容：运行根目录增加本地统一配置回退；提交、比较、模板、会话告警和驾驶舱服务统一重定位逻辑运行路径；Flow 允许读取共享运行根目录内的绝对文件；正式任务配置收敛为最小指针和必要覆盖；驾驶舱失败报告迁入模块输出目录；截图流程在启动 Excel 前确认系统默认打印机，并移除打印端口后缀依赖。
- 涉及文件：`backend/services/prefect_runner.py`、`flows/notify_single_flow.py`、`services/runtime_paths.py`、`services/{commit,compare,template,session_alert,dashboard_v2_trigger,dashboard_failure_report,screenshot}_service.py`、`config/modules/wecom_sender.json`、报表与任务发布配置、`tests/test_*.py`、`PROJECT_GUIDE.md`。
- 配置或迁移：无需迁移业务数据；确认 `config/runtime.local.json` 中的 `runtime.root` 指向 `C:\AutoNotifyRuntime`，并确认系统已安装名为 `Microsoft Print to PDF` 的打印机。旧驾驶舱失败报告保留在原目录，不自动搬迁。
- 验证：运行相关服务与 Flow 的聚焦 Pytest、Ruff 检查和 `git diff --check`。
- 风险与回滚：截图会持续保留 `Microsoft Print to PDF` 为系统默认打印机，可能影响同一 Windows 用户的手工打印；回滚截图服务和发送配置可恢复旧行为，路径变更应与任务配置和相关服务整体回滚。

### 2026-07-13 - 运行时目录文档对齐

- 原因：运行目录文档只描述业务产物，遗漏 `prefect/prefect_home` 与 `processes`，根 README 还保留旧目录与自动迁移说明。
- 修改内容：统一记录运行时完整目录树、敏感会话数据边界和进程登记使用方式；移除已失效的旧路径说明。
- 涉及文件：`scripts/README.md`、`README.md`、`PROJECT_GUIDE.md`、`tests/test_development_environment_contract.py`。
- 配置或迁移：无；旧状态迁移仍仅能在停栈后人工执行。
- 验证：`python -m pytest -p no:cacheprovider tests/test_development_environment_contract.py -q`，`git diff --check`。
- 风险与回滚：仅文档与契约测试变更，不修改运行目录或会话数据。

### 2026-07-13 - 驾驶舱工具项目根路径修复

- 原因：`scripts/tools/dashboard/` 下的 Python 工具将 `scripts/` 误判为项目根目录，直接执行时无法导入 `services`、`infrastructure` 等项目模块。
- 修改内容：统一工具脚本的项目根目录层级；新增直接执行 `--help` 的目录契约测试，并补充驾驶舱工具的用途和风险说明。
- 涉及文件：`scripts/tools/dashboard/*.py`、`scripts/README.md`、`tests/test_development_environment_contract.py`、`PROJECT_GUIDE.md`。
- 配置或迁移：无。
- 验证：`python -m pytest -p no:cacheprovider tests/test_development_environment_contract.py::test_dashboard_tools_can_run_directly_from_the_repository_root -q`，`python -m pytest -p no:cacheprovider tests/test_dashboard_v2_admin_scripts.py -q`，`python -m compileall -q scripts/tools/dashboard`。
- 风险与回滚：仅修正脚本自定位路径，不改变数据库、会话或导入导出参数；回滚相应脚本即可恢复旧行为。

### 2026-07-13 - 脚本入口与诊断工具归类

- 原因：`scripts/` 根目录混有日常运行入口、兼容转发和人工诊断脚本，README 还将内部 Web 启动模块误写为根入口。
- 修改内容：将 City Ops 下钻诊断脚本归入 `scripts/dev/`；明确 `scripts/lib/start_web.ps1` 是内部开发/诊断模块，日常运行只使用四个根入口。
- 涉及文件：`scripts/dev/test_city_ops_drilldown.py`、`scripts/README.md`、`README.md`、`tests/test_development_environment_contract.py`、`PROJECT_GUIDE.md`。
- 配置或迁移：无；本机 `.local.ps1` 覆盖文件路径保持不变。
- 验证：`python -m pytest -p no:cacheprovider tests/test_development_environment_contract.py -q`，PowerShell 语法解析与 `git diff --check`。
- 风险与回滚：外部手工调用诊断脚本时需改用新路径；恢复原路径即可回滚，不影响日常运行入口。

### 2026-07-13 - 强制运行时目录收敛

- 原因：仓库 `runtime/`、旧共享目录与 Flow 临时目录并存，容易混用 Cookie、浏览器状态和任务产物。
- 修改内容：统一为 `session`、`modules`、`flow` 三类目录；移除旧路径读取兼容与启动时迁移；模块发送配置收敛为不含业务报表内容的通用默认值。
- 涉及文件：`services/runtime_paths.py`、`scripts/lib/runtime_state_migration.ps1`、`config/modules/*.json`、`flows/notify_single_flow.py`、`PROJECT_GUIDE.md`、相关测试。
- 配置或迁移：停止运行栈后人工执行一次迁移；旧 Cookie/Profile 不再保留。首次启动可能需要重新登录。
- 验证：`python -m pytest tests/test_runtime_paths.py tests/test_runtime_state_migration_powershell.py tests/test_flow_helpers.py -q`。
- 风险与回滚：该目录迁移不兼容，回滚必须恢复同版本代码与配置，不能混用新旧状态目录。

每次修改后新增一条，格式如下：

```markdown
### YYYY-MM-DD - 变更标题

- 原因：
- 修改内容：
- 涉及文件：
- 配置或迁移：
- 验证：
- 风险与回滚：
```

### 2026-07-13 - 通报任务配置收敛与驾驶舱分区修复

- 原因：每份通报任务重复维护相同的步骤与运行目录，且驾驶舱未来分区缺失会使健康检查失败。
- 修改内容：新增通报任务默认模板，任务文件仅保留报表和差异配置；Flow 在运行时生成任务专属中间文件路径；补齐驾驶舱未来分区。
- 涉及文件：`config/task_defaults/notify.json`、`config/tasks/*.json`、`flows/notify_single_flow.py`、`tests/test_flow_helpers.py`、`README.md`。
- 配置或迁移：本机登录覆盖使用 `retain_after_login`；已执行一次 Dashboard V2 分区维护。
- 验证：`python -m pytest -p no:cacheprovider tests/test_flow_helpers.py -q`，`python -m ruff check flows/notify_single_flow.py tests/test_flow_helpers.py`，Dashboard V2 readiness 为 ready。
- 风险与回滚：回滚模板加载与任务文件即可恢复每任务独立步骤配置；分区维护只补齐未来分区，不删除有效业务数据。

### 2026-07-13 - 启动不再同步 Prefect Deployment

- 原因：自动通报后续新增的 Deployment 不保证同步写入 `prefect.yaml`，日常启动覆盖部署会导致配置漂移。
- 修改内容：移除 `scripts/run.ps1` 的 `prefect deploy --all`；启动不再发布或同步仓库中的 Deployment 定义。当前启动清理会临时暂停并恢复受管 Deployment，并校正 Notify Deployment 的 Work Pool。
- 涉及文件：`scripts/run.ps1`、`README.md`、`PROJECT_GUIDE.md`、启动环境契约测试。
- 配置或迁移：无。需要按仓库配置新建或更新 Deployment 时，人工执行 `prefect deploy --all`。
- 验证：`python -m pytest -p no:cacheprovider tests/test_development_environment_contract.py -q`。
- 风险与回滚：仓库中的 `prefect.yaml` 不再自动反映到 Prefect；如需恢复自动同步，恢复启动脚本中的部署命令。

### 2026-07-11 - 统一数据库本地配置

- 原因：Prefect PostgreSQL 与驾驶舱 MySQL 连接信息分散在多个本地 PowerShell 文件中，不利于迁移和维护。
- 修改内容：新增统一 `config/runtime.local.json` 入口和可提交模板；加载器优先读取 JSON，旧 PowerShell profile 作为回退。
- 涉及文件：`config/runtime.local.example.json`、`scripts/lib/prefect_env_prod.ps1`、`scripts/tools/dashboard/mysql_env.ps1`、`README.md`。
- 配置或迁移：填写被忽略的 `config/runtime.local.json`，不提交密码。
- 验证：`python -m pytest -p no:cacheprovider tests/test_development_environment_contract.py -q`。
- 风险与回滚：删除统一文件即可恢复旧 `.local.ps1` 回退逻辑。

### 2026-07-12 - 驾驶舱数据库收敛为 V2

- 原因：V1 与 V2 链路并存，增加运行配置、迁移和状态检查的维护成本。
- 修改内容：驾驶舱触发器、运行记录和状态检查迁移至 V2 命名空间；项目约定仅支持 `schema_version: 2` 与 `dashboard_v2`。
- 涉及文件：`services/dashboard_v2_trigger.py`、`infrastructure/dashboard_v2_run_store.py`、`backend/routers/status.py`、`models/dashboard_v2_base.py`、`config/dashboard/session.json`。
- 配置或迁移：使用 `alembic -c alembic_dashboard_v2.ini upgrade head`；本地运行配置中的驾驶舱数据库为 `dashboard_v2`。
- 验证：`PYTHONPATH=. pytest tests/test_status_router.py -q`，预期通过。
- 风险与回滚：该变更移除了 V1 兼容运行路径；如需恢复，必须从 V1 删除前的提交整体回退，不能混用两套迁移链。

### 2026-07-12 - Prefect 统一测试数据库与运行依赖修复

- 原因：合并项目后需要使用独立的 Prefect 测试库；旧启动逻辑固定派生 `prefect_dev`，且 Prefect 3.7 在过新的 FastAPI/Starlette 组合下创建 Work Pool 会返回 HTTP 500。
- 修改内容：Prefect 直接使用 `prefect.postgres.url` 指向的数据库；运行配置和部署统一使用 `default-agent-pool`；FastAPI 限制为 0.115 系列并重建锁文件。
- 涉及文件：`scripts/lib/prefect_start.ps1`、`scripts/lib/prefect_env_prod.ps1`、`scripts/dev/env.ps1`、`pyproject.toml`、锁文件、`prefect.yaml`、部署配置和测试。
- 配置或迁移：在被忽略的 `config/runtime.local.json` 中配置目标 PostgreSQL 测试库；不迁移旧 Prefect 数据；移除遗留的 Work Pool。
- 验证：环境契约测试 19 项通过；Prefect API health 为 200；`default-agent-pool` 为 READY 且 Worker ONLINE。
- 风险与回滚：切换回旧库只需恢复本地 URL 并重启；正式环境应新建独立空库，不能合并旧 Prefect 内部表。

### 2026-07-12 - Session Keeper 运维约定

- 原因：共享会话保活、故障分类和业务刷新需要统一的 Windows 运维边界。
- 修改内容：记录保留 Edge、固定十五分钟调度、登录重试、基础设施失败处理，以及共享 Session Manager 和全局登录锁约束。
- 涉及文件：`README.md`、`PROJECT_GUIDE.md`。
- 配置或迁移：无；运行时私密覆盖仍不得纳入版本控制。
- 验证：执行 Session Keeper 聚焦测试、全量测试、JSON/YAML 解析、敏感信息扫描和 `git diff --check`。
- 风险与回滚：本次仅修改文档；回滚相应文档提交即可。

### 2026-07-13 - 三 Work Pool Windows 运维流程

- 原因：单 Pool 说明已不符合 Session、Dashboard、Notify 分池部署和统一共享运行目录。
- 修改内容：记录手工启动、`1 / 4 / 6` Pool 拓扑、积压取消、Worker 监督、Server 手工恢复、共享 Excel/登录锁及受管进程停止边界。
- 涉及文件：`README.md`、`PROJECT_GUIDE.md`、`scripts/setup_windows_env.ps1`、`tests/test_development_environment_contract.py`。
- 配置或迁移：在被忽略的 `config/runtime.local.json` 中维护 `C:\AutoNotifyRuntime` 与三 Pool 配置；正式 Notify 上限为 6。
- 验证：全量 Pytest 为 `620 passed, 14 skipped`；聚焦积压策略、锁和受管进程模拟为 `98 passed, 21 deselected`；PowerShell 语法解析、`prefect.yaml` YAML 解析、Ruff 和 `git diff --check` 均通过。由于被 Git 忽略的 `config/runtime.local.json`、数据库配置和凭据均缺失，未执行真实 Windows 启动、三个在线 Worker、Notify 排队、Excel 串行和单登录所有者验收。
- 风险与回滚：回滚文档和 setup 变更不会停止已运行服务；运行进程只通过当前 Worktree 的注册记录管理。

### 2026-07-24 - 自动登录浏览器 Profile 动态清理

- 原因：保留的专用 Edge 在冷启动或 Worker 非正常停止后可能仍持有 Profile；旧逻辑会把 `taskkill` 失败和进程查询失败误判为清理成功，导致首轮 WebDriver 启动争用同一 Profile。
- 修改内容：将浏览器清理改为按专用 Profile 定向识别、验证 `taskkill` 退出码并条件轮询退出状态；清理无法确认时保留状态并以 `browser_cleanup_failed` 结束本轮。WebDriver 对明确的 Profile 释放窗口错误实施短轮询，不使用该路径上的固定 60 秒等待；Manager 成为唯一清理所有者，登录子进程跳过重复关闭。
- 涉及文件：`services/browser_session.py`、`services/session_manager.py`、`services/login_service.py`、`tests/test_browser_session.py`、`tests/test_session_manager.py`、`tests/test_login_service.py`、`README.md`、`PROJECT_GUIDE.md`。
- 配置或迁移：无。继续使用现有 `browser_close_wait_seconds`（默认 10 秒）作为条件等待上限；专用 Profile、Cookie 和运行时状态均不迁移、不删除。
- 验证：`python -m pytest -p no:cacheprovider tests/test_browser_session.py tests/test_login_service.py tests/test_session_manager.py tests/test_session_keeper_flow.py -q` 为 `104 passed`；Ruff 和 `git diff --check` 通过。全量 Pytest 另有 1 个未修改的健康检查断言与现有接口字段不同步。
- 风险与回滚：Worker 账户缺少 WMI 进程查询权限时将安全失败并报告 `browser_cleanup_failed`，不会自动抢占 Profile；回滚上述会话源码与文档即可恢复旧行为，但会重新引入错误的关闭成功判定。

### 2026-07-25 - Session Keeper 无固定等待重试与安全阶段诊断

- 原因：`judicious-seriema` 在 Profile 清理成功后仍以泛化 `browser_error` 失败，而 Keeper 代码显式覆盖为单次登录，导致随后可恢复的瞬态失败无法自动执行第二次完整登录；父进程仅保留泛化类别，无法定位子登录失败发生在驱动启动、门户还是会话捕获阶段。
- 修改内容：Keeper 改为使用配置的两次完整登录上限；`login_retry_delay_seconds` 固定为 `0`，不再执行固定 `60` 秒睡眠，下一次尝试前复用 Profile 条件清理。登录子进程仅输出阶段、异常类名和白名单原因码，父进程严格验证后附加到固定安全摘要；原始 stderr/stdout 继续丢弃。
- 涉及文件：`flows/session_keeper_flow.py`、`services/session_manager.py`、`services/login_service.py`、`config/modules/autologin.json`、相关会话测试、README 与 PROJECT_GUIDE。
- 配置或迁移：将已提交的 `config/modules/autologin.json` 中 `login_retry_delay_seconds` 改为 `0`；无需迁移 Cookie、Profile 或会话状态。Notify Flow 继续显式使用单次完整登录。
- 验证：新增 Keeper 参数、零延迟、清理失败短路和安全诊断标记测试；执行聚焦会话测试、Ruff 和 `git diff --check`。
- 风险与回滚：第一次非清理失败会额外执行一次完整登录，可能增加一次门户请求；Profile 未释放、进程查询失败和关闭失败仍安全停止。回滚本条变更需同时恢复 Keeper 调用、零延迟配置、会话源码和文档，不能仅恢复延迟值。
