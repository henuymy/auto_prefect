# 运行配置 JSON 化与脚本收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用一个本地 JSON 配置和三个生命周期命令统一启动、停止、查看通报与驾驶舱系统。

**Architecture:** `scripts/lib/runtime_config.ps1` 将 `config/runtime.local.json` 映射为现有服务所用环境变量。`scripts/run.ps1` 编排 Prefect、Worker、deployment 同步与 Web，旧入口只做兼容转发；低频驾驶舱工具迁入 `scripts/tools/dashboard/`。

**Tech Stack:** PowerShell、Python 3.13、Prefect 3.7、FastAPI、React/Vite、PostgreSQL、MySQL。

## Global Constraints

- `config/runtime.local.json` 必须被 Git 忽略，模板不包含密码。
- `run.ps1` 仅启动服务并同步 Cron，不自动执行通报或采集。
- 现有 `scripts/environment.local.ps1` 与旧 profile 只在 JSON 缺失时回退。
- 迁移完成前，旧启动命令必须继续可用。

---

### Task 1: JSON 配置加载器

**Files:**
- Create: `config/runtime.local.example.json`
- Create: `scripts/lib/runtime_config.ps1`
- Modify: `.gitignore`
- Modify: `tests/test_development_environment_contract.py`

- [ ] 写失败测试，断言 JSON 模板包含 `prefect.postgres.url`、`dashboard.mysql.host`、`runtime.work_pool`，且 `.gitignore` 忽略 `config/runtime.local.json`。
- [ ] 运行 `python -m pytest -p no:cacheprovider tests/test_development_environment_contract.py -q`，预期因模板和加载器缺失失败。
- [ ] 实现 `Import-RuntimeConfig`：优先读取 JSON，验证必填字段，并导出 `AUTO_NOTIFY_PREFECT_DATABASE_URL`、`DASHBOARD_MYSQL_*`、`PREFECT_API_URL` 与 work pool。
- [ ] 运行契约测试和 PowerShell 解析检查，预期通过。

### Task 2: 统一运行入口

**Files:**
- Create: `scripts/run.ps1`
- Create: `scripts/stop.ps1`
- Create: `scripts/status.ps1`
- Modify: `scripts/prefect_start.ps1`
- Modify: `scripts/start_web.ps1`
- Modify: `scripts/dev/start.ps1`
- Modify: `scripts/dev/stop.ps1`
- Modify: `scripts/dev/status.ps1`

- [ ] 写失败测试，断言 `run.ps1` 加载 `runtime_config.ps1`、调用 Prefect Server/Worker、同步 deployments、启动 Web，且不调用 flow run。
- [ ] 运行该测试，预期因新入口不存在失败。
- [ ] 实现三个入口；`run.ps1` 依序校验连接、启动 Prefect、`prefect deploy --all`、启动 Worker 和 Web；旧 `dev` 入口仅转发。
- [ ] 运行契约测试与 PowerShell 语法检查，预期通过。

### Task 3: 工具归档与文档

**Files:**
- Move: `scripts/dashboard/*` -> `scripts/tools/dashboard/*`
- Modify: 所有引用旧工具路径的 PowerShell、README、`PROJECT_GUIDE.md`

- [ ] 写失败测试，断言低频导入、导出、审计、迁移脚本位于 `scripts/tools/dashboard/`，生命周期入口未引用旧目录。
- [ ] 移动工具并更新所有引用路径；保留 `scripts/dashboard/README.md` 兼容指向或移除无引用的旧目录。
- [ ] 更新 README 与 `PROJECT_GUIDE.md`，记录 JSON 配置、三个入口、Cron 安全语义和迁移回退。
- [ ] 运行 Python 测试、Ruff、npm typecheck、npm build；提交所有变更。
