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

- 提供可提交的 `*.example.*` 模板。
- 提供被 Git 忽略的 `*.local.*` 实际配置文件。
- 统一本地配置入口，避免数据库、端口和凭据分散在多个脚本。
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
- 数据库：Prefect PostgreSQL（测试库使用 `prefect_test`）；驾驶舱 MySQL V2（`dashboard_v2`）。
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

其中维护 Prefect PostgreSQL 与驾驶舱 MySQL V2 的地址、端口、数据库、账号和密码。`prefect.postgres.url` 直接指定 Prefect 使用的数据库，不再从 `/prefect` 派生 `/prefect_dev`。当前测试库名为 `prefect_test`；后续正式切换时创建新的空库并只修改该 URL。驾驶舱配置 `config/dashboard/session.json` 固定使用 `schema_version: 2` 和 `dashboard_v2`；配置值不应写入 README、本文件或 Git 提交。

Windows 运行时固定使用 `C:\AutoNotifyRuntime`，避免代码升级、分支或 Worktree 切换产生多套 Cookie、Edge Profile、锁和进程注册。运行根目录解析优先级为环境变量 `AUTO_NOTIFY_RUNTIME_ROOT`、`config/runtime.local.json` 中的 `runtime.root`、默认值 `C:\AutoNotifyRuntime`；生产环境应让后两者保持一致，环境变量只用于测试或受控诊断。Prefect 拓扑固定为 `windows-session-pool`、`windows-dashboard-pool`、`windows-notify-pool`，并发上限分别为 `1 / 4 / 6`；`prefect.yaml`、Deployment 和 `runtime.work_pools` 必须保持一致。

运行时目录固定为 `C:\AutoNotifyRuntime`。业务逻辑只允许使用受控逻辑路径：`runtime/session/...`（Cookie、会话健康状态、Edge Profile、锁与登录调试）、`runtime/config/{drafts,versions}/...`（未发布草稿与正式配置历史）、`runtime/{logs,health,starter_templates,temp}/...`（运行日志、健康探针、新手模板中间产物和工具临时文件）、`runtime/modules/<模块名>/output/...`（模块独立产物）和 `runtime/flow/<任务名>/{output,backup,debug,tmp}/...`（Flow 产物）。它们分别映射到共享目录的同名顶级目录。运行栈另外维护 `prefect/prefect_home`（本机 Prefect Home）和 `processes`（受管进程登记 JSON）；它们不是业务产物，禁止被 Flow 当作输入/输出目录。

```text
C:\AutoNotifyRuntime\
  session\
    cookie_dump.json
    browser-session.json
    browser-profile\
    session-health.json
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

这是一次不兼容目录切换：禁止读取、复制或回退到仓库 `runtime/`、`runtime/cookies`、`runtime/browser_session` 或其他未分类路径。业务配置中的 `runtime/...` 必须通过统一路径服务映射到共享运行根目录；Flow 内部只可接受共享运行根目录下的绝对路径，并继续拒绝根目录外的绝对路径、`../` 与旧目录格式。一次性迁移只在人工执行 `Invoke-RuntimeStateMigration` 时移动 Cookie、会话健康状态、浏览器 Profile、草稿、配置版本、日志、健康检查、新手模板中间产物和登录调试文件；同名目标冲突时保留源目录，不自动覆盖。`setup_windows_env.ps1` 与 `run.ps1` 不得自动迁移旧状态。

`setup_windows_env.ps1` 预建 `session\locks`、`config\{drafts,versions}`、`modules`、`flow`、`health`、`logs`、`starter_templates` 和 `temp` 骨架。首次登录由 Session Manager 在 `session\browser-profile` 创建 Profile，并在 `session` 下写入 Cookie 与健康状态。

`pyproject.toml` 中的 FastAPI 必须保持在 `>=0.110.0,<0.116`。Prefect 3.7 与更高的 FastAPI/Starlette 路由接口不兼容；更新依赖时通过 `requirements.lock` 和 `requirements-dev.lock` 重建并安装精确版本。

### 通报任务配置约定

- `config/task_defaults/notify.json` 保存通报任务的公共步骤、模块配置入口和默认等待参数。
- 已发布的 `config/tasks/<任务名>.json` 默认只保存 `flow_name` 与 `report_config_path`；只有偏离公共默认值的等待或步骤参数才写入覆盖项，禁止重新复制整套默认配置。
- 草稿、安全测试和真实试跑配置写入共享运行目录 `runtime/config/drafts`。API 返回路径时不得假设文件一定在仓库内；仓库外的共享运行文件使用可直接定位的绝对路径。
- 提交、比较、模板更新、会话告警和驾驶舱服务读取 `runtime/...` 时必须复用统一运行路径解析，不得各自拼接仓库根目录。驾驶舱失败报告统一写入 `runtime/modules/dashboard/output/failure_reports`。

### 驾驶舱数据库约定

- 驾驶舱只保留 V2 数据模型、服务、运行记录与迁移链路，不支持 V1 运行时分派。
- 数据库迁移唯一入口为：`alembic -c alembic_dashboard_v2.ini upgrade head`。
- 运行状态使用 V2 collection-run 存储；状态接口和任务入口不得导入 V1 run store 或 V1 trigger 路径。
- 驾驶舱模块产物写入 `runtime/modules/dashboard/output/...`；历史迁移审核包仅能作为离线输入或输出，默认目录为 `runtime/modules/dashboard/output/v2_migration`，不得作为日常 Flow 的运行依赖。

### 会话生命周期约定

Session Keeper 和所有业务 Flow 必须复用共享 Session Manager 与全局登录锁。不得新增绕过该管理器或锁的直接登录入口；当前登录使用无头模式并保留同一浏览器 Profile 与进程，业务重试仅允许在明确的会话失效后强刷新一次并重试失败步骤一次。

### 运行入口

环境初始化、启动、状态和停止入口为：

```powershell
pwsh -File scripts/setup_windows_env.ps1
pwsh -File scripts/run.ps1
pwsh -File scripts/status.ps1
pwsh -File scripts/stop.ps1
```

系统只允许专用 Windows 用户在保持登录和交互式桌面会话时手工启动，不配置开机自启。主机重启、用户重新登录或 Prefect Server 停止后，运维人员必须再次执行 `scripts/run.ps1`。该脚本启动一个 Server、三个 Worker、FastAPI 和 React 前端，应用 Pool 上限 `1 / 4 / 6`，并主动提交一次 Session Keeper；它不发布、同步或修改任何 Prefect Deployment。通报与驾驶舱采集仍由既有 Prefect Deployment 的 Cron 执行，不会自动触发全部通报。

自动调度的 Notify Run 比预计时间晚超过 10 分钟时取消，手工 Notify Run 保留。过期 Session Keeper 和高频 Dashboard Run 不补跑。处于 `RUNNING`、`CANCELLING` 或 `PAUSED` 的本项目 Run 会阻止替代 Worker 启动，必须人工处理。Worker 监督进程在崩溃 30 秒后重启 Worker；Prefect Server 需要手工执行 `scripts/run.ps1` 恢复。`scripts/stop.ps1` 只能停止 `C:\AutoNotifyRuntime\processes` 中已登记且身份匹配的进程。

`scripts/status.ps1` 从 Prefect API 读取配置中的 Pool 名称和实际并发上限，并列出排队超过 10 分钟的自动调度 Run。锁协议当前不记录等待者，状态输出必须明确显示 `waiters=unavailable`，不得声称能展示等待数量。

旧 Notify Pool 上若仍有保留的手工、宽限期或 PENDING Run，启动必须列出 Run 身份并失败关闭。Prefect 3.7 无法安全改派单个已排队 Run，也不能为共享旧 Pool 自动启动不受 Run ID 约束的 Worker；运维人员应先受控排空或取消列出的旧 Run。由于 Prefect Server 已注册，随后必须执行 `scripts/stop.ps1` 再运行 `scripts/run.ps1`，或直接执行 `scripts/run.ps1 -ForceRestart`。禁止删除 Run 强行完成切换。

所有 Excel COM 阶段通过 `C:\AutoNotifyRuntime\session\locks\excel_com.lock` 串行，登录刷新通过 `C:\AutoNotifyRuntime\session\locks\login.lock` 串行；提高 Notify 并发不得绕过这两个锁。

截图流程启动 Excel 前必须先把 Windows 默认打印机切换为配置的 `Microsoft Print to PDF`，再创建 COM 实例，避免 Excel 继承 RustDesk 等虚拟打印机。打印机配置使用系统打印机名称，不写端口后缀；切换成功后不自动恢复旧默认打印机，因此专用 Windows 用户不应依赖其他默认打印机。

## 四、变更记录

### 2026-07-14 - Runtime 路径收口与驾驶舱 V2-only 清理

- 原因：共享运行目录迁移后，新手模板清单和配置删除仍尝试生成仓库相对路径；驾驶舱还保留 V1 任务分派、手工入口、数据库模型、迁移链和旧输出目录。
- 修改内容：增加统一路径展示和运行根目录绝对路径校验；修复草稿删除、新手模板和迁移工具的共享 Runtime 路径；抽离 V2 所需的采集、结构比对、指标和运行记录通用能力；删除 V1 运行代码、迁移、手工工具与测试；任务、手工采集、健康检查和 MySQL 本机覆盖均固定为 V2。
- 涉及文件：`services/runtime_paths.py`、`backend/services/{config_store,starter_template,prefect_runner,health_service}.py`、`services/dashboard_*`、`infrastructure/dashboard_run_protocol.py`、`tasks/dashboard_tasks.py`、`scripts/tools/dashboard/`、`models/dashboard_*.py`、`migrations/dashboard/`、V2 测试与项目文档。
- 配置或迁移：当前运行环境只允许 `dashboard_v2` 和 `alembic_dashboard_v2.ini`；已移除的 V1 Alembic 链不能用于回滚。历史迁移包工具保留为离线审计能力，默认写入共享模块输出目录。
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
- 修改内容：移除 `scripts/run.ps1` 的 `prefect deploy --all`；启动只恢复服务和 Worker，不修改现有 Deployment。
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
