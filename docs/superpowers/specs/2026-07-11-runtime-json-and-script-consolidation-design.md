# 运行配置 JSON 化与脚本收敛设计

## 目标

将项目运行配置收敛到一个被 Git 忽略的 JSON 文件，并提供唯一的启动、停止和状态入口，使通报与驾驶舱共用同一套 Prefect Server、Worker、数据库配置和生命周期管理。

## 配置

- 可提交模板：`config/runtime.local.example.json`。
- 实际本机配置：`config/runtime.local.json`，Git 忽略，不提交任何密码。
- JSON 包含 Prefect PostgreSQL、驾驶舱 MySQL、端口、Work Pool、前端启动开关和运行模式。
- 新增 `scripts/lib/runtime_config.ps1`：读取 JSON、验证必填字段、将值注入当前进程和子进程的环境变量。
- 旧 `scripts/environment.local.ps1` 与分散的 `*.local.ps1` 在迁移期仅作回退；启动命令优先 JSON，并输出迁移提示。

## 脚本结构

```text
scripts/
  run.ps1                 一键启动 Prefect、Worker、API、前端并同步部署
  stop.ps1                停止运行栈
  status.ps1              显示端口、API、Prefect 与数据库状态
  lib/
    python_env.ps1        Python 解释器解析
    runtime_config.ps1    JSON 配置加载与校验
  services/
    prefect.ps1           Prefect Server/Worker 生命周期内部实现
    web.ps1               FastAPI/React 生命周期内部实现
  tools/
    dashboard/            导入、导出、审计、迁移、压测等低频工具
```

运行入口只保留 `run.ps1`、`stop.ps1`、`status.ps1`。旧入口保留轻量兼容包装，在文档中标记迁移期限，避免一次重构破坏既有使用习惯。

## 启动语义

`scripts/run.ps1` 执行以下顺序：

1. 加载并校验 `config/runtime.local.json`。
2. 验证 PostgreSQL 与 MySQL 连通性。
3. 启动 Prefect Server 和共用 Worker。
4. 同步 `prefect.yaml` 中的通报与驾驶舱 deployments。
5. 启动 FastAPI 与 React 前端。

启动仅同步既有 Cron，不立即运行通报或驾驶舱采集，防止重复企业微信通知和无意写入业务数据。手动运行由 Prefect UI 或显式命令执行。

## 验证与迁移

- 契约测试验证 JSON 模板、忽略规则、三个入口和配置加载器。
- 单元测试覆盖 JSON 缺失、字段缺失与字段到环境变量的映射。
- PowerShell 语法检查全部新入口和加载器。
- Python 测试、前端类型检查和构建维持通过。
- README 与 `PROJECT_GUIDE.md` 记录配置路径、唯一入口和每次变更的迁移说明。
