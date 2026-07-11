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
- API：FastAPI、Uvicorn。
- 数据库：Prefect PostgreSQL；驾驶舱 MySQL V2（`dashboard_v2`）。
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

其中维护 Prefect PostgreSQL 与驾驶舱 MySQL V2 的地址、端口、数据库、账号和密码。驾驶舱配置 `config/dashboard/session.json` 固定使用 `schema_version: 2` 和 `dashboard_v2`；配置值不应写入 README、本文件或 Git 提交。

### 驾驶舱数据库约定

- 驾驶舱只保留 V2 数据模型、服务、运行记录与迁移链路，不支持 V1 运行时分派。
- 数据库迁移唯一入口为：`alembic -c alembic_dashboard_v2.ini upgrade head`。
- 运行状态使用 V2 collection-run 存储；状态接口和任务入口不得导入 V1 run store 或 V1 trigger 路径。

### 运行入口

当前开发环境总入口为：

```powershell
pwsh -File scripts/run.ps1
```

它启动 Prefect Server、Worker、FastAPI 和 React 前端。通报与驾驶舱采集通过 Prefect deployment 的 Cron 执行；启动服务不会自动触发全部通报，避免重复发送通知。停止和状态检查分别使用 `scripts/stop.ps1` 与 `scripts/status.ps1`。

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
