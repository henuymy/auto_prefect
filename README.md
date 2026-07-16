# 自动通报系统

基于 Prefect、FastAPI 和 React 的自动化报表通报与数据驾驶舱。系统负责登录业务平台、采集或下载报表、比对并更新 Excel 模板、发送企业微信通知，同时提供任务配置、运行日志和驾驶舱查询界面。

## 运行链路

```text
Prefect Flow
  → 登录或复用会话
  → 采集/下载业务数据
  → 校验、比对并更新模板
  → 生成截图与通知内容
  → 企业微信发送
  → 成功后提交正式模板
```

驾驶舱采集链路独立写入 MySQL，经 FastAPI 提供查询接口，由 React 前端展示实时值、累计值、历史数据和目标完成情况。

## 目录结构

```text
.
├── backend/          # FastAPI 应用、路由和后台服务
├── frontend/         # React + Vite 配置中心与数据驾驶舱
├── flows/            # Prefect Flow 编排入口
├── tasks/            # Prefect Task 封装
├── services/         # 登录、采集、比对、通知和驾驶舱业务逻辑
├── infrastructure/   # MySQL、文件存储、Excel、企业微信等适配器
├── models/           # 领域模型与 SQLAlchemy 模型
├── migrations/       # Alembic 数据库迁移
├── config/           # 模块、报表、任务和驾驶舱配置
├── prefect.yaml      # Session Keeper、驾驶舱等系统级 Prefect 部署声明
├── scripts/          # 环境、服务、迁移和运维脚本
├── tests/            # Python 自动化测试
├── utils/            # 配置、日期和请求解析工具
└── templates/        # 本地业务模板，不纳入版本控制
```

运行时 Cookie、会话、日志和临时报表位于 `C:\AutoNotifyRuntime`，不要提交、公开或随意清空。自动通报 Deployment 由前端“发布到调度”按钮直接发布到 Prefect，不使用独立 `deployments/` 文件。

## 环境要求

- Windows 10/11
- Conda `base` 环境，Python 3.13
- NVM 管理的 Node.js 20 LTS
- MySQL 8（仅驾驶舱功能需要）
- Prefect Server 3.7（仅本地运行 Prefect 服务与调度需要）
- Microsoft Edge（自动登录与网页采集需要）；涉及 Excel COM 的比对、模板更新和截图功能还需 Microsoft Excel

## 安装

在 Conda `base` 环境安装锁定的 Python 开发依赖：

```powershell
conda activate base
python -m pip install -r requirements-dev.lock
```

通过 NVM 安装并启用前端所需的 Node.js 20 LTS，再安装前端依赖：

```powershell
nvm install 20.20.1
nvm use 20.20.1
cd frontend
npm install
```

也可以使用项目初始化脚本完成 Python 依赖、运行目录和本机组件检查：

```powershell
pwsh -File scripts/setup_windows_env.ps1
```

私密配置使用同名 `.local.json` 或 `.local.ps1` 文件覆盖，切勿把账号、Cookie、Webhook、数据库密码写入受版本控制的文件。

## 依赖文件职责

| 文件 | 职责 |
| --- | --- |
| `pyproject.toml` | Python 项目的直接运行依赖、开发可选依赖和工具配置的唯一声明来源。 |
| `requirements.txt` | Python 运行时安装入口，引用 `requirements.lock`。 |
| `requirements.lock` | 由 `pyproject.toml` 解析生成的 Python 运行时精确版本清单。 |
| `requirements-dev.lock` | 由 `pyproject.toml` 的开发额外依赖解析生成，供开发、测试和质量检查安装。 |
| `frontend/package.json` | 前端的直接依赖、开发依赖与 npm 脚本声明。 |
| `frontend/package-lock.json` | npm 解析出的前端精确版本清单，保证不同机器安装结果一致。 |

锁文件会记录直接依赖及其传递依赖，因此行数通常远多于 `pyproject.toml` 和 `frontend/package.json` 中列出的直接依赖；不要手动逐项编辑锁文件。

## 配置

- `config/reports/*.json`：通报业务配置，前端正式保存的位置。
- `config/task_defaults/notify.json`：通报 Flow 的公共步骤、模块配置和默认运行参数。
- `config/tasks/*.json`：通报任务的报表标识与少量差异覆盖；运行时自动合并公共默认值。
- `config/modules/*.json`：登录、下载、比对、模板和发送模块配置。
- `config/dashboard/session.json`：驾驶舱采集、数据库、并发与留存策略。
- `scripts/tools/dashboard/`：驾驶舱导入、导出、审计与迁移等低频工具。

### 腾讯文档表格下载

下载配置中的报表可设为 `source: "tencent_sheet"`，并指定 `doc_url`（或
`file_id`）和至少一个 `sheets` 项。腾讯文档凭据保存在被 Git 忽略的
`config/modules/tencent_docs.local.json`；可提交的
`config/modules/tencent_docs.json` 仅保留字段模板，不能填入真实凭据。

`sheets[].range` 支持显式 A1 范围和自动范围。使用空值、`auto`、`used` 或
`used_range` 时，系统优先按腾讯文档返回的实际数据行列边界下载；缺少该边界时
才回退到工作表容量。填写 `A1:Z1000` 等显式范围时会按原范围请求，不会被元数据
预先截断；若后续分块超出数据区，接口返回无效范围后将停止继续读取该 Sheet。

```json
{
  "source": "tencent_sheet",
  "name": "腾讯文档日报",
  "doc_url": "https://docs.qq.com/sheet/<文档标识>?tab=<Sheet ID>",
  "sheets": [
    {"sheet_id": "<Sheet ID>", "range": "auto", "output_sheet_name": "日报"}
  ]
}
```

常用本地覆盖示例：

```text
config/modules/login_config.local.json
config/modules/wecom_sender.local.json
config/runtime.local.json
```

### 腾讯文档与智能表格

前端“数据抓取”分为“业务接口”“腾讯文档”和“智能表格”三个独立标签；后两个标签共用
腾讯文档 OpenAPI 凭据，但配置契约不同：

| 数据源 | `source` | 子表选择 | 读取方式 |
| --- | --- | --- | --- |
| 普通腾讯 Sheet | `tencent_sheet` | `sheet_id` 或 `sheet_name` | `range` 为 `auto` 或显式 A1 范围 |
| 腾讯智能表格 | `tencent_smartbook` | `sheet_id` 或 `sheet_name` | 导出全部字段与全部分页记录，禁止设置 `range` |

智能表格的 `sheet_id` 可直接使用链接中 `tab` 参数的值；同时填写 `sheet_id` 和
`sheet_name` 时以 ID 为准。`sheets[]` 的配置顺序就是导出工作簿中各输出 Sheet 的顺序，
`output_sheet_name` 可改写输出 Sheet 名。字段标题写入表头，选项、人员、链接等富值会转换为
可读文本；没有 `values` 的记录不会写入工作簿。

腾讯文档 OpenAPI 凭据仅存放在被 Git 忽略的
`config/modules/tencent_docs.local.json`，必须包含 `client_id`、`access_token` 与 `open_id`；
前端不会读取或上传这些凭据。智能表格配置示例：

```json
{
  "credentials": {
    "client_id": "<Client-Id>",
    "access_token": "<Access-Token>",
    "open_id": "<Open-Id>"
  }
}
```

```json
{
  "source": "tencent_smartbook",
  "name": "智能表格日报",
  "doc_url": "https://docs.qq.com/smartsheet/Dexample?tab=sheet-1",
  "output_filename": "智能表格日报.xlsx",
  "sheets": [
    {
      "sheet_id": "sheet-1",
      "output_sheet_name": "汇总"
    }
  ]
}
```

可在不改动业务配置的情况下手动诊断一个子表；命令只输出导出结果的摘要，不会打印凭据：

```powershell
python scripts/dev/test_tencent_smartbook_export.py --doc-url "https://docs.qq.com/smartsheet/Dexample?tab=sheet-1" --sheet "id:sheet-1" --output "temp/smartbook.xlsx"
```

### 统一运行配置

将 `config/runtime.local.example.json` 复制为 `config/runtime.local.json`，在该文件中统一维护 Prefect PostgreSQL、驾驶舱 MySQL、Prefect API 地址和 Work Pool。该本地文件已被 Git 忽略，不能提交。

启动脚本只读取 JSON 配置。
`prefect.postgres.url` 指定 Prefect 直接使用的 PostgreSQL 数据库；测试环境可使用
`prefect_test`，正式环境可切换为新的干净数据库。
运行配置必须声明固定共享目录 `C:\AutoNotifyRuntime` 和三个 Process Work Pool：

| Work Pool | 并发上限 | 运行内容 |
| --- | ---: | --- |
| `windows-session-pool` | 1 | Session Keeper |
| `windows-dashboard-pool` | 4 | 驾驶舱采集、同步和维护 Flow |
| `windows-notify-pool` | 6 | 完整通报 Flow |

每次启动会先取消本项目全部已过期的排队 `SCHEDULED` / `PENDING` Run，再启动并确认 Session Worker 在线，唯一一次提交新的 Session Keeper Run，最后启动 Dashboard 和 Notify Worker；提交不会等待 Session Keeper Flow 完成。`RUNNING`、`CANCELLING` 或 `PAUSED` 的 Run 仍只会在 `scripts/run.ps1 -ForceRestart` 时取消。

运行时目录由 `config/runtime.local.json` 的 `runtime.root` 指定，默认是
`C:\AutoNotifyRuntime`：

```text
C:\AutoNotifyRuntime\
  session\
    stages\                 已验证的业务阶段会话快照
    stage_health.json        阶段会话健康状态
    browser-session.json
    browser-profile\
    locks\
      login.lock
      excel_com.lock
      dashboard_collection.lock
    login_debug\
  config\
    drafts\                  前端保存、尚未发布的通报配置草稿
    versions\<配置名>\        正式配置覆盖前的历史备份
  logs\
    web_runs.jsonl           Web/后端运行日志
  health\                    健康检查探针
  starter_templates\         新手模板生成的中间产物
  modules\<模块名>\output\   模块独立产物
  flow\<任务名>\
    output\
    backup\
    debug\
    tmp\
  temp\                      工具临时文件
  prefect\prefect_home\      Prefect Home；本机 Server 与 Worker 共用
  processes\                 受管 Server、Worker、后端和前端的进程登记 JSON
```

所有配置、API 路径和运行文件记录都使用相对于 `runtime.root` 的受控路径，例如
`session/...`、`config/...`、`modules/<模块名>/output/...` 或
`flow/<任务名>/tmp/...`。不得传递绝对路径、`..` 或历史 `runtime/...` 前缀；
这些值会被拒绝，而不会回退到仓库目录。

`session/stages/`、`browser-profile/` 和 `browser-session.json` 是敏感登录态，
不能提交、公开、复制或随意删除。`cookie_dump.json` 只可由 Session Broker 在
内部兼容步骤中临时创建，并在操作结束后删除；业务 Flow 和运维脚本不得直接读取它。
`processes/` 是 `scripts/stop.ps1` 安全识别本项目进程的依据；停止运行栈前
不得手工删除登记文件。`prefect/prefect_home/` 与这些共享状态不随代码升级、
分支或 Worktree 切换。旧仓库 `runtime/` 路径不再参与启动；如需迁移旧状态，
只能在停止运行栈后人工执行 `Invoke-RuntimeStateMigration`。

`setup_windows_env.ps1` 与 `run.ps1` 使用同一个机器级运行时互斥锁，禁止
setup/setup 或 setup/startup 并发修改共享目录。

项目固定 `Prefect 3.7.0`，并将 FastAPI 限制在 `0.115` 系列以避免与较新
Starlette 路由接口不兼容。安装或更新依赖时请使用 `requirements.lock`。

## 启动

启动必须由专用 Windows 用户手工执行。该用户需要保持登录和交互式桌面会话；主机重启或用户重新登录后，必须再次执行 `scripts/run.ps1`。Prefect Server 停止后也不会自动恢复，需要人工重新运行启动脚本。

标准操作顺序：

```powershell
pwsh -File scripts/setup_windows_env.ps1
pwsh -File scripts/run.ps1
pwsh -File scripts/status.ps1
pwsh -File scripts/stop.ps1
```

`scripts/run.ps1` 先取消本项目全部已过期的排队 `SCHEDULED` / `PENDING` Run，再启动并确认 Session Worker，唯一一次提交新的 Session Keeper Run，随后启动 Dashboard 和 Notify Worker；该提交不等待 Flow 完成。脚本不会发布、同步或修改 Prefect Deployment；通报和驾驶舱采集仍由既有 Cron 调度执行，不会自动触发全部通报。`scripts/status.ps1` 从 Prefect API 读取配置中的三个 Pool 名称和实际并发上限，因此验收期 Notify 上限为 2 时会显示 2；它也逐条标识排队超过 10 分钟的自动调度 Run。锁文件当前不记录等待者，状态只显示所有者和持有时长，并明确显示 `waiters=unavailable`。`scripts/stop.ps1` 仅停止 `C:\AutoNotifyRuntime\processes` 中登记且进程身份匹配的本项目进程。

升级时若旧 Notify Pool 仍有未过期的保留 Run，启动脚本会列出 Deployment、Run ID、状态和旧 Pool 并拒绝启动。Prefect 3.7 不支持安全改派单个已排队 Run，且启动旧 Pool Worker 可能执行同 Pool 的无关工作；运维人员应先在受控条件下用旧 Pool Worker 排空或取消列出的 Run。此时 Prefect Server 已注册，处理后先执行 `scripts/stop.ps1` 再运行 `scripts/run.ps1`。`scripts/run.ps1 -ForceRestart` 只取消运行中的 Run，不能绕过未过期旧队列的失败关闭。不得删除历史 Run 或把它们静默遗留在无 Worker 的 Pool。

一键启动本地 Prefect、API 和前端：

```powershell
pwsh -File scripts/run.ps1
```

查看或停止服务：

```powershell
pwsh -File scripts/status.ps1
pwsh -File scripts/stop.ps1
```

仅在本地开发或诊断时，可直接调用内部 Web 启动脚本：

```powershell
pwsh -File scripts/lib/start_web.ps1 -Mode both
```

日常运行仍应使用 `scripts/run.ps1`；`scripts/lib/` 下的脚本是实现模块，不作为稳定的运维入口。

默认地址：

- 前端：`http://127.0.0.1:5173`
- 后端健康检查：`http://127.0.0.1:8000/api/health`
- Prefect UI：`http://127.0.0.1:4200`

## Session Keeper

Session Keeper 仅支持 Windows 部署，依赖持续存活的 Microsoft Edge 用户会话；日常探活不应关闭该浏览器。Prefect Deployment 名称为 `session-keeper-flow/session-keeper`，固定在 `Asia/Shanghai` 时区每 10 分钟运行。

- Session Keeper 与业务 Flow 都通过 `StageSessionBroker` 获取会话。Keeper 在同一个 Edge Profile 中预热 `report_analysis`、`smart_ops`、`city_ops` 与 `data_market` 阶段，业务 Flow 复用已验证的阶段数据；下载期间明确认证失效时仍可在全局登录锁内刷新一次，并仅重试失败下载一次。
- 每次 Session Keeper 成功完成预热后，Flow 日志会按阶段记录共享会话健康确认，便于在 Prefect UI 中核验本轮健康状态。
- `autologin.json` 中每个 stage 默认配置一个探活；需要更严格的鉴权校验时可配置 `probes` 数组，所有启用探活都成功才判定该 stage 健康。探活必须动态读取当前会话的 Cookie 或 Storage，不得提交固定认证材料。
- 认证明确失效时只在全局登录锁内执行一次完整刷新，并仅重试失败的业务步骤一次；基础设施探活失败不触发登录。
- Session Keeper 不发送会话失败或恢复通知。development 环境仅在最终业务 Run 失败时，按工作负载、业务标识和 Flow Run 去重后发送一次企业微信告警。
- 完整登录仅持久化调用方声明的业务阶段会话；调用方只消费 Broker 返回的内存 `stage_data`，不得重新读取 Cookie 文件。
- 请求状态码、探活原因与自动恢复边界见 [请求故障分类与处理](docs/request-failure-handling.md)。
- 会话告警去重状态保存在 `session/session-alerts/incident_state.json`；开发环境业务 Run 告警状态保存在 `session/business-alerts/incident_state.json`。两者都会映射到共享运行根目录，而不会写入代码仓库。在 Prefect UI 中打开上述 Deployment 查看最新运行，或执行 `python -m prefect flow-run ls --flow-name session-keeper-flow --limit 1`。
- 支持日志不得复制账号密码、Cookie、Token、Webhook 值或其他认证材料。

发布统一 Keeper 后，必须在 Prefect UI 或命令行删除旧的远端部署及其调度；仅从 `prefect.yaml` 删除不会清理服务端已有对象：

```powershell
python -m prefect deployment delete "session-keeper-flow/session-keeper-report"
python -m prefect deployment delete "session-keeper-flow/session-keeper-city"
```

先确认新的 `session-keeper-flow/session-keeper` 已发布并可运行，再执行上述一次性清理。`scripts/run.ps1` 不发布或删除 Deployment。

## 积压与故障恢复

- 每次启动都会取消本项目全部已过期的排队 `SCHEDULED` / `PENDING` Run，不区分自动或手工触发；取消后不补跑 Session Keeper、Dashboard、Notify 或维护任务。
- `RUNNING`、`CANCELLING` 或 `PAUSED` 的本项目 Run 不会在普通启动时自动清理，并会阻止启动替代 Worker；只有 `scripts/run.ps1 -ForceRestart` 才会取消这些运行中的 Run。
- Worker 崩溃后由各自的监督进程等待 30 秒再重启；Prefect Server 停止后不自动重启，使用 `scripts/run.ps1` 手工恢复。
- `windows-notify-pool` 允许通报并行，但所有 Excel COM 阶段必须通过 `C:\AutoNotifyRuntime\session\locks\excel_com.lock` 串行；所有会话刷新通过 `C:\AutoNotifyRuntime\session\locks\login.lock` 保证只有一个登录所有者。

## 主要入口

```text
flows/notify_single_flow.py                  单个通报任务
flows/dashboard_metric_flow.py               驾驶舱指标采集
flows/dashboard_partition_maintenance_flow.py 驾驶舱分区维护
backend/app.py                                FastAPI 应用
frontend/src/main.tsx                         配置中心前端
frontend/src/dashboard-main.tsx               数据驾驶舱前端
```

需要用仓库配置新建或更新部署时，手工发布 Prefect 部署：

```powershell
prefect deploy --all
```

## 数据库迁移

驾驶舱仅使用 V2 Alembic 配置：

```powershell
alembic -c alembic_dashboard_v2.ini upgrade head
```

执行迁移前必须确认连接的是目标环境，并先完成数据库备份。

## 测试与质量检查

```powershell
python -m pytest -q
python -m ruff check .
cd frontend
npm run typecheck
npm run build
```

带 `mysql_integration` 标记的测试需要显式配置可丢弃的测试数据库；常规测试不会自动连接生产数据库。

## 运维约定

- 正式调度使用 PostgreSQL 作为 Prefect 元数据库；SQLite 仅用于本地调试。
- 真实试跑会发送企业微信消息，执行前检查接收范围和 Webhook。
- 正式模板仅在通知发送成功后提交；不要绕过提交门禁。
- 修改数据库结构必须新增 Alembic 迁移，不直接改生产表。
- Cookie、Edge Profile、共享锁、Prefect Home、日志和临时运行数据统一写入 `C:\AutoNotifyRuntime`；仓库内旧 Cookie/Profile 只作为首次安全迁移的来源，目标已存在时绝不覆盖。
- 前端构建产物和依赖目录可随时重新生成，不纳入版本控制。

## 故障定位

1. 运行 `scripts/status.ps1` 检查进程和端口。
2. 请求 `/api/live` 判断 API 进程是否存活，再请求 `/api/health` 检查依赖。
3. 查看 `C:\AutoNotifyRuntime\logs` 和 Prefect Flow Run 日志。
4. 登录失败时检查共享会话状态、Cookie 有效期和被忽略的本地登录配置，不输出认证材料。
5. 驾驶舱异常时检查 MySQL 连接、Alembic 版本和最近一次采集运行状态。

## 安全边界

仓库中的示例配置不得包含生产密钥。提交前检查 `git status`，确保 `.local.*`、`runtime/`、`templates/`、Cookie、证书和数据库凭据没有进入暂存区。
