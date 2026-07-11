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
├── deployments/      # Prefect 部署定义
├── scripts/          # 环境、服务、迁移和运维脚本
├── tests/            # Python 自动化测试
├── utils/            # 配置、日期和请求解析工具
├── runtime/          # 本地运行数据，不纳入版本控制
└── templates/        # 本地业务模板，不纳入版本控制
```

`runtime/` 可能包含 Cookie、会话、日志和临时报表。不要提交、公开或随意清空该目录。

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
- `config/tasks/*.json`：Prefect Flow 的任务入参。
- `config/modules/*.json`：登录、下载、比对、模板和发送模块配置。
- `config/dashboard/session.json`：驾驶舱采集、数据库、并发与留存策略。
- `scripts/tools/dashboard/`：驾驶舱导入、导出、审计与迁移等低频工具。

常用本地覆盖示例：

```text
config/modules/login_config.local.json
config/modules/wecom_sender.local.json
config/runtime.local.json
scripts/prefect_env_prod.local.ps1
```

### 统一运行配置

将 `config/runtime.local.example.json` 复制为 `config/runtime.local.json`，在该文件中统一维护 Prefect PostgreSQL、驾驶舱 MySQL、Prefect API 地址和 Work Pool。该本地文件已被 Git 忽略，不能提交。

启动脚本优先使用 JSON；仅在 JSON 缺失时，才回退读取
`scripts/environment.local.ps1` 和旧的 `scripts/prefect_env_prod.local.ps1`。
`prefect.postgres.url` 指定 Prefect 直接使用的 PostgreSQL 数据库；测试环境可使用
`prefect_test`，正式环境可切换为新的干净数据库。
当前测试配置使用 `49.233.78.70:5432/prefect_test`，默认 Work Pool 为
`default-agent-pool`。首次执行 `scripts/run.ps1` 会初始化 Prefect 元数据、创建
Work Pool 并同步仓库中的部署；不会迁移旧项目的 Prefect 历史数据。

项目固定 `Prefect 3.7.0`，并将 FastAPI 限制在 `0.115` 系列以避免与较新
Starlette 路由接口不兼容。安装或更新依赖时请使用 `requirements.lock`。

## 启动

`scripts/run.ps1` 只启动服务并同步 Prefect deployment；通报和驾驶舱采集仍由既有 Cron 调度执行，不会自动触发全部通报。

一键启动本地 Prefect、API 和前端：

```powershell
pwsh -File scripts/run.ps1
```

查看或停止服务：

```powershell
pwsh -File scripts/status.ps1
pwsh -File scripts/stop.ps1
```

只启动配置中心和驾驶舱：

```powershell
pwsh -File scripts/start_web.ps1 -Mode both
```

默认地址：

- 前端：`http://127.0.0.1:5173`
- 后端健康检查：`http://127.0.0.1:8000/api/health`
- Prefect UI：`http://127.0.0.1:4200`

## 主要入口

```text
flows/notify_single_flow.py                  单个通报任务
flows/dashboard_metric_flow.py               驾驶舱指标采集
flows/dashboard_partition_maintenance_flow.py 驾驶舱分区维护
backend/app.py                                FastAPI 应用
frontend/src/main.tsx                         配置中心前端
frontend/src/dashboard-main.tsx               数据驾驶舱前端
```

发布 Prefect 部署：

```powershell
prefect deploy --all
```

## 数据库迁移

旧驾驶舱和 V2 驾驶舱使用独立 Alembic 配置：

```powershell
alembic -c alembic_dashboard.ini upgrade head
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
- 日志、下载文件、截图和会话数据统一写入 `runtime/`。
- 前端构建产物和依赖目录可随时重新生成，不纳入版本控制。

## 故障定位

1. 运行 `scripts/status.ps1` 检查进程和端口。
2. 请求 `/api/live` 判断 API 进程是否存活，再请求 `/api/health` 检查依赖。
3. 查看 `runtime/logs/` 和 Prefect Flow Run 日志。
4. 登录失败时检查 `runtime/browser_session/`、Cookie 有效期和本地登录配置。
5. 驾驶舱异常时检查 MySQL 连接、Alembic 版本和最近一次采集运行状态。

## 安全边界

仓库中的示例配置不得包含生产密钥。提交前检查 `git status`，确保 `.local.*`、`runtime/`、`templates/`、Cookie、证书和数据库凭据没有进入暂存区。
