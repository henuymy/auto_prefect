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

Windows 运行时固定使用 `C:\AutoNotifyRuntime`，避免代码升级、分支或 Worktree 切换产生多套 Cookie、Edge Profile、锁和进程注册。Prefect 拓扑固定为 `windows-session-pool`、`windows-dashboard-pool`、`windows-notify-pool`，并发上限分别为 `1 / 4 / 6`；`prefect.yaml`、Deployment 和 `runtime.work_pools` 必须保持一致。

`pyproject.toml` 中的 FastAPI 必须保持在 `>=0.110.0,<0.116`。Prefect 3.7 与更高的 FastAPI/Starlette 路由接口不兼容；更新依赖时通过 `requirements.lock` 和 `requirements-dev.lock` 重建并安装精确版本。

### 驾驶舱数据库约定

- 驾驶舱只保留 V2 数据模型、服务、运行记录与迁移链路，不支持 V1 运行时分派。
- 数据库迁移唯一入口为：`alembic -c alembic_dashboard_v2.ini upgrade head`。
- 运行状态使用 V2 collection-run 存储；状态接口和任务入口不得导入 V1 run store 或 V1 trigger 路径。

### 会话生命周期约定

Session Keeper 和所有业务 Flow 必须复用共享 Session Manager 与全局登录锁。不得新增绕过该管理器或锁的直接登录入口；Windows 运行时应保留 Edge 用户会话，业务重试仅允许在明确的会话失效后强刷新一次并重试失败步骤一次。

### 运行入口

环境初始化、启动、状态和停止入口为：

```powershell
pwsh -File scripts/setup_windows_env.ps1
pwsh -File scripts/run.ps1
pwsh -File scripts/status.ps1
pwsh -File scripts/stop.ps1
```

系统只允许专用 Windows 用户在保持登录和交互式桌面会话时手工启动，不配置开机自启。主机重启、用户重新登录或 Prefect Server 停止后，运维人员必须再次执行 `scripts/run.ps1`。该脚本启动一个 Server、三个 Worker、FastAPI 和 React 前端，应用 Pool 上限 `1 / 4 / 6`，并主动提交一次 Session Keeper；通报与驾驶舱采集仍由 Prefect Deployment 的 Cron 执行，不会自动触发全部通报。

自动调度的 Notify Run 比预计时间晚超过 10 分钟时取消，手工 Notify Run 保留。过期 Session Keeper 和高频 Dashboard Run 不补跑。处于 `RUNNING`、`CANCELLING` 或 `PAUSED` 的本项目 Run 会阻止替代 Worker 启动，必须人工处理。Worker 监督进程在崩溃 30 秒后重启 Worker；Prefect Server 需要手工执行 `scripts/run.ps1` 恢复。`scripts/stop.ps1` 只能停止 `C:\AutoNotifyRuntime\processes` 中已登记且身份匹配的进程。

所有 Excel COM 阶段通过 `C:\AutoNotifyRuntime\locks\excel_com.lock` 串行，登录刷新通过 `login.lock` 串行；提高 Notify 并发不得绕过这两个锁。

## 四、变更记录

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
- 验证：运行全量 Pytest、PowerShell 语法解析、`prefect.yaml` 解析及受控 Windows 验收。
- 风险与回滚：回滚文档和 setup 变更不会停止已运行服务；运行进程只通过当前 Worktree 的注册记录管理。
