# Dashboard V2 Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除驾驶舱数据库 V1 实现并将运行路径固定为 V2。

**Architecture:** 保留 V2 模型、迁移、API 和流水线。V2 使用 `DashboardV2Base` 与 `dashboard_v2_run_store`，删除 V1 模型、迁移、服务、脚本和所有 `schema_version` 分派。

**Tech Stack:** Python 3.11、SQLAlchemy 2、Alembic、FastAPI、Prefect、pytest、PowerShell。

## Global Constraints

- 仅支持 `schema_version: 2` 和 `dashboard_v2` 数据库。
- 保留 V2 API、前端契约和 V2 表结构。
- Python 测试统一以 `PYTHONPATH=.` 运行。

---

### Task 1: V2 基础依赖与配置校验

**Files:** `models/dashboard_v2_base.py`、`models/dashboard_v2.py`、`services/dashboard_v2_trigger.py`、`backend/routers/status.py`、`tests/test_dashboard_v2_trigger.py`、`tests/test_dashboard_v2_run_store.py`；删除 `services/dashboard_trigger.py` 与 `tests/test_dashboard_trigger.py`。

- [ ] 将 `services/dashboard_trigger.py` 与 `tests/test_dashboard_trigger.py` 重命名为 V2 路径；在 `tests/test_dashboard_v2_trigger.py` 新增 `test_load_dashboard_config_rejects_non_v2_schema`：写入 `{"schema_version": 1}`，断言 `load_dashboard_config()` 抛出匹配“只支持.*2”的 `ValueError`。
- [ ] 运行 `$env:PYTHONPATH='.'; pytest tests/test_dashboard_v2_trigger.py -q`，确认新测试因当前配置允许 V1 而失败。
- [ ] 将 `NAMING_CONVENTION` 移至 `models/dashboard_v2_base.py`；将 V2 模型仅绑定 `DashboardV2Base`。
- [ ] 在 `load_dashboard_config()` 中将缺失或非 2 的版本拒绝为 `ValueError("dashboard schema_version 只支持 2")`。
- [ ] 将 `services/dashboard_v2_trigger.py` 和 `backend/routers/status.py` 的运行记录读写改为 `infrastructure.dashboard_v2_run_store.MySQLV2CollectionRunStore`，不再导入 V1 store；将其余调用方改为 V2 触发器路径。
- [ ] 运行 `$env:PYTHONPATH='.'; pytest tests/test_dashboard_v2_trigger.py tests/test_dashboard_v2_run_store.py -q`，预期通过；提交 `refactor: isolate dashboard v2 runtime dependencies`。

### Task 2: 固定 V2 任务、健康检查与运维入口

**Files:** `tasks/dashboard_tasks.py`、`backend/services/health_service.py`、`scripts/lib/prefect_start.ps1`、`scripts/tools/dashboard/run_collection.py`、`tests/test_dashboard_v2_tasks.py`、`tests/test_health_service.py`；删除 `tests/test_dashboard_tasks.py`。

- [ ] 将 `tests/test_dashboard_tasks.py` 重命名为 `tests/test_dashboard_v2_tasks.py`，新增 V1 配置调用 `run_dashboard_metric_task.fn()` 抛出 V2-only 配置错误的测试，并先运行确认失败。
- [ ] 删除 `tasks/dashboard_tasks.py` 对 `dashboard_pipeline` 和 `dashboard_indicator_sync` 的 import 与 V1 pipeline 字典；仅将 `INDICATOR_SYNC` 路由到 `execute_dashboard_v2_indicator_sync`，其他模式路由到 `execute_dashboard_v2_pipeline`。
- [ ] 健康检查在 MySQL 正常时始终调用 `check_dashboard_v2_schema()`；配置读取失败时返回失败状态。
- [ ] 删除 Prefect 启动脚本中 `dashboard-collection`、`dashboard-daily-acc`、`dashboard-monthly`、`dashboard-indicator-sync` 部署名，保留 V2 维护部署。
- [ ] 运行 `$env:PYTHONPATH='.'; pytest tests/test_dashboard_v2_tasks.py tests/test_health_service.py -q`，预期通过；提交 `refactor: make dashboard execution v2 only`。

### Task 3: 删除 V1 源码、迁移和测试

**Files:** 删除 `alembic_dashboard.ini`、`migrations/dashboard/`、`models/dashboard_base.py`、所有不含 `_v2_` 的 `models/dashboard_*.py`、`infrastructure/dashboard_run_store.py`、所有不含 `_v2_` 的 V1 `services/dashboard_*.py` 与对应 `tests/test_dashboard_*.py`；Task 1-2 已迁移的 V2 触发器和任务测试除外。

- [ ] 先运行 `rg -n "dashboard_base|dashboard_run_store|execute_dashboard_pipeline|schema_version.*1" --glob '*.py'`，列出待消除的 V1 引用。
- [ ] 用 `git rm` 删除上述 V1 文件和目录，保留名称含 `_v2_` 的模块、迁移、测试以及共享 `infrastructure/dashboard_mysql.py`。
- [ ] 再次运行同一 `rg` 命令，预期无输出；运行 `$env:PYTHONPATH='.'; pytest --collect-only -q`，预期无 V1 导入错误。
- [ ] 提交 `refactor: remove dashboard v1 implementation`。

### Task 4: 配置、脚本与文档收敛

**Files:** `config/dashboard/session.json`、`scripts/dashboard/mysql_env.local.ps1`、`scripts/tools/dashboard/mysql_env.ps1`、`scripts/environment.local.example.ps1`、`README.md`、`PROJECT_GUIDE.md`；删除 `scripts/dashboard/mysql_env.v1.local.ps1`。

- [ ] 在触发器测试中加入真实 `session.json` 的断言：`config["schema_version"] == 2`；运行该单测，预期通过。
- [ ] 将所有默认数据库改为 `dashboard_v2`，删除 V1 环境文件与 V1 配置说明。
- [ ] README 和项目指南只保留 `alembic -c alembic_dashboard_v2.ini upgrade head`，删除 V1 Alembic 命令。
- [ ] 运行 `rg -n "alembic_dashboard\.ini|mysql_env\.v1|schema_version.*1" README.md PROJECT_GUIDE.md config scripts`，预期无输出；提交 `docs: document dashboard v2-only operations`。

### Task 5: 全量验证

- [ ] 运行 `git diff --check` 与 `rg -n "dashboard_base|dashboard_run_store|schema_version.*1" --glob '*.py'`，预期均无输出。
- [ ] 运行 `$env:PYTHONPATH='.'; pytest -q`，预期所有测试通过。
- [ ] 运行 `alembic -c alembic_dashboard_v2.ini heads`，预期仅显示 V2 的 `20260630_0001` head。
- [ ] 提交可能的最终测试修正，提交信息为 `test: verify dashboard v2-only cleanup`。
