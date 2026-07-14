# 脚本目录

`scripts/` 只保留项目运行入口、内部脚本库与受控诊断工具。日常操作只使用根目录的四个入口：

| 脚本 | 用途 |
| --- | --- |
| `setup_windows_env.ps1` | 安装依赖并按 `config/runtime.local.json` 初始化运行目录。 |
| `run.ps1` | 启动 Prefect Server、三个 Worker 与 Web 服务。 |
| `status.ps1` | 查看受管进程、端口、Pool、会话和锁状态。 |
| `stop.ps1` | 停止受管运行栈。 |

## 子目录

- `lib/`：仅供入口脚本加载的 PowerShell/Python 实现，不直接作为日常命令运行。
- `tools/`：一次性维护、导入导出和诊断工具；`tools/dashboard/` 是驾驶舱专项工具。
- `dev/`：开发环境、模拟与诊断脚本，不能用于生产运行栈；包括
  `test_city_ops_drilldown.py` 等会调用真实业务接口的人工诊断工具。
- `legacy/`：旧公网栈和计划任务脚本，仅为兼容保留。根目录的
  `start_public_stack.ps1`、`stop_public_stack.ps1` 只转发到这里，不应用于当前三 Pool 架构。
- `dashboard/`：被忽略的本机驾驶舱连接覆盖文件；不提交其中的 `*.local.ps1`。

## 完整脚本索引

### 根目录与本机覆盖

| 文件 | 用途 |
| --- | --- |
| `start_public_stack.ps1` | 已废弃公网栈的兼容启动转发入口。 |
| `stop_public_stack.ps1` | 已废弃公网栈的兼容停止转发入口。 |
| `environment.local.example.ps1` | 旧式本机环境变量覆盖模板。 |
| `environment.local.ps1` | 本机环境变量实际值，已忽略。 |
| `prefect_env_prod.local.ps1` | 旧式 Prefect 连接覆盖，已忽略。 |
| `dashboard/mysql_env.local.ps1` | 默认驾驶舱 MySQL 本机覆盖，已忽略。 |
| `dashboard/mysql_env.v2.local.ps1` | V2 驾驶舱 MySQL 本机覆盖，已忽略。 |

### 内部库 `lib/`

| 文件 | 用途 |
| --- | --- |
| `python_env.ps1` | 统一定位项目 Python 解释器。 |
| `runtime_config.ps1` | 读取 `config/runtime.local.json` 并导出运行时环境变量。 |
| `process_registry.ps1` | 登记、校验和停止受管进程，并维护启动互斥锁。 |
| `prefect_env_prod.ps1` | 兼容加载 Prefect 运行环境配置。 |
| `prefect_start.ps1` | 启动 Prefect Server 或 Worker，并登记后台进程。 |
| `prefect_stop.ps1` | 停止 Prefect 相关进程的底层实现。 |
| `prefect_startup_reconcile.py` | 启动前检查和清理过期调度任务，阻止不安全的旧 Pool 切换。 |
| `prefect_status.py` | 从 Prefect API 汇总 Pool Worker、运行和积压任务状态。 |
| `runtime_state_migration.ps1` | 人工迁移旧运行时 Cookie、会话状态和浏览器 Profile。 |
| `start_web.ps1` | 单独启动 FastAPI 与 Vite Web 服务，供开发或诊断使用。 |

### 开发与诊断 `dev/`

| 文件 | 用途 |
| --- | --- |
| `env.ps1` | 加载开发环境变量和旧配置兼容项。 |
| `prefect_env_debug.ps1` | 提供 Prefect 本地调试配置。 |
| `start.ps1` | 转发到根目录 `run.ps1`。 |
| `status.ps1` | 转发到根目录 `status.ps1`。 |
| `stop.ps1` | 转发到根目录 `stop.ps1`。 |
| `simulate_clean_concurrent_login.ps1` | 模拟并发登录和干净会话场景。 |
| `test_city_ops_drilldown.py` | 使用真实 City Ops 会话执行下钻请求并导出 Excel；仅人工诊断使用。 |

### 旧公网栈 `legacy/`

| 文件 | 用途 |
| --- | --- |
| `public_stack.ps1` | 旧的公网 Prefect、Web 与 FRP 运行栈总控脚本。 |
| `start_public_stack.ps1` | 调用旧公网栈启动流程。 |
| `stop_public_stack.ps1` | 调用旧公网栈停止流程。 |
| `install_public_stack_schedule.ps1` | 安装旧公网栈计划任务。 |
| `uninstall_public_stack_schedule.ps1` | 移除旧公网栈计划任务。 |

## 驾驶舱工具

`tools/dashboard/` 中的 Python 工具必须从仓库根目录运行，并自行解析项目根目录：

```powershell
python scripts/tools/dashboard/run_collection.py --help
```

| 文件 | 用途 |
| --- | --- |
| `mysql_env.ps1` | 加载驾驶舱 MySQL 配置，优先使用统一 JSON 配置。 |
| `mysql_env.example.ps1` | MySQL 覆盖配置模板。 |
| `run_v2_mysql_tests.ps1` | 使用可丢弃的 V2 测试库运行 MySQL 集成测试。 |
| `create_v2_databases.sql.example` | 创建 V2 正式库与测试库的 SQL 示例。 |
| `run_collection.py` | 手工执行一批 Dashboard V2 采集任务。 |
| `render_flow.ps1` | 渲染驾驶舱执行流程图 PNG。 |
| `v2_cutover_audit.py` | 只读检查 V2 切换前置条件、Worker 和部署状态。 |
| `initialize_v2_hierarchy.py` | 初始化 V2 城市、分公司和网格层级。 |
| `import_v2_indicator_config.py` | 导入审核后的 V2 指标和自定义公式配置。 |
| `import_v2_target_plan.py` | 从 JSON 导入并激活 V2 指标目标方案。 |
| `export_indicator_candidates.py` | 从报表配置扫描并导出候选指标。 |
| `export_metric_sample.py` | 导出单个指标的原始采集样本，不写 MySQL。 |
| `export_v2_migration_bundle.py` | 离线导出历史迁移审核包；不能用于当前 Dashboard V2 运行链。 |
| `generate_metric_target_template.py` | 基于区域和指标参考数据生成目标值 Excel 模板。 |

这些工具可能读取生产会话、访问数据库或写出 Excel；执行前确认目标环境和输入文件，不要把它们接入日常启动入口。

## 运行目录

脚本产生的状态不写入项目目录。运行根目录由
`config/runtime.local.json` 的 `runtime.root` 指定，默认是
`C:\AutoNotifyRuntime`：

```text
session/                    Cookie、浏览器 Profile、会话状态和锁
prefect/prefect_home/       Prefect Home；本机 Server 与 Worker 共享
processes/                  受管 Server、Worker、后端和前端的进程登记 JSON
modules/<module>/output/    模块独立运行产物
flow/<task>/{output,backup,debug,tmp}/
                            Flow 任务产物
logs/、temp/                启动和工具按需产生的临时运行文件
```

`session/` 包含 `cookie_dump.json`、`browser-profile/`、
`browser-session.json` 与 `locks/`；其中 Cookie 和
浏览器 Profile 是敏感登录态，不能提交、复制或随意删除。`processes/` 仅供
`stop.ps1` 和 `status.ps1` 识别本项目进程，停止运行栈前不得手工删除其中的
登记文件。`prefect/prefect_home/` 不随代码分支或 Worktree 切换，应与运行根目录
一起保留。

不要新增根级运行脚本、项目内 `runtime/` 路径或未分类的 `scripts/` 工具；应将实现放入对应子目录，并从现有入口调用。
