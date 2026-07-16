# Prefect 启动时清理过期任务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 服务启动时取消所有已有的过期排队任务，只在清理后提交一次新的 Session Keeper Run。

**Architecture:** `prefect_startup_reconcile.py` 继续负责暂停受管 Deployment、读取排队 Run、取消选中的 Run 并恢复 Deployment，但选择规则改为与部署类别和创建方式无关的统一过期判定。`run.ps1` 在启动 Session Worker 后提交新的 Session Keeper，再启动 Dashboard 与 Notify Worker；运行中任务仍只由 `-ForceRestart` 取消。

**Tech Stack:** Python 3.13、Prefect 3.7 客户端、PowerShell、pytest、Ruff。

## Global Constraints

- 过期定义为 `SCHEDULED` 或 `PENDING` Run 的 `expected_start_time` 早于启动清理时刻。
- 自动调度和 Prefect 页面手动提交的过期 Run 必须同样取消。
- 未来计划、没有 `expected_start_time` 的 Run、非受管 Deployment 及未使用 `-ForceRestart` 的运行中 Run 必须保留。
- 启动清理只能操作 `classify_deployment()` 识别的受管 Deployment。
- 新 Session Keeper 必须在清理完成、Session Worker 上线后只提交一次。

---

### Task 1: 统一过期排队 Run 的选择规则

**Files:**
- Modify: `scripts/lib/prefect_startup_reconcile.py:42-147, 189-243, 337-397`
- Modify: `tests/test_prefect_startup_reconcile.py:47-166`

**Interfaces:**
- Consumes: `run.expected_start_time`、`run.deployment_id`、`DeploymentPolicy` 映射和启动时刻 `datetime`。
- Produces: `should_cancel_run(run: Any, now: datetime) -> tuple[bool, str]`，以及 `select_runs_to_cancel(runs, deployment_policies, now) -> list[tuple[Any, str]]`；取消原因固定为 `startup_overdue_run`。

- [ ] **Step 1: 写入失败的过期选择测试**

在 `tests/test_prefect_startup_reconcile.py` 用以下测试替换现有的按 Notify 宽限期和 Dashboard 单例保留策略的测试：

```python
def test_all_overdue_managed_runs_are_cancelled_regardless_of_policy_or_origin():
    runs = [
        scheduled_run(run_id="session", deployment_id="session", minutes_late=1),
        scheduled_run(run_id="dashboard", deployment_id="dashboard", minutes_late=600),
        scheduled_run(
            run_id="manual-notify",
            deployment_id="notify",
            minutes_late=30,
            auto_scheduled=False,
        ),
    ]

    selected = select_runs_to_cancel(
        runs,
        {
            "session": DeploymentPolicy.SESSION,
            "dashboard": DeploymentPolicy.DASHBOARD_SINGLETON,
            "notify": DeploymentPolicy.NOTIFY,
        },
        NOW,
    )

    assert [(run.id, reason) for run, reason in selected] == [
        ("session", "startup_overdue_run"),
        ("dashboard", "startup_overdue_run"),
        ("manual-notify", "startup_overdue_run"),
    ]
```

保留并调整未来任务测试，使用 `select_runs_to_cancel(..., NOW)` 验证 `minutes_late=-1` 的自动和手动 Run 均不被选中；新增缺少 `expected_start_time` 的 Run 也不会被选中。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest -q tests/test_prefect_startup_reconcile.py
```

Expected: FAIL，原因是当前 `select_runs_to_cancel()` 仍需要 `notify_grace_seconds`，并保留 Dashboard 单例和手动 Notify Run。

- [ ] **Step 3: 最小化实现统一选择规则**

在 `scripts/lib/prefect_startup_reconcile.py` 中删除 `is_manual_run()` 和按 `DeploymentPolicy` 分支的宽限期判断，将核心逻辑收敛为：

```python
def should_cancel_run(run: Any, now: datetime) -> tuple[bool, str]:
    expected_start = getattr(run, "expected_start_time", None)
    if expected_start is None or not _is_overdue(run, now):
        return False, "scheduled_run_preserved"
    return True, "startup_overdue_run"


def select_runs_to_cancel(
    runs: Iterable[Any],
    deployment_policies: Mapping[Any, DeploymentPolicy],
    now: datetime,
) -> list[tuple[Any, str]]:
    return [
        (run, "startup_overdue_run")
        for run in runs
        if getattr(run, "deployment_id", None) in deployment_policies
        and should_cancel_run(run, now)[0]
    ]
```

从 `reconcile_client()`、`_run_cli()` 和 `main()` 移除 `notify_grace_seconds` 参数；从 `parse_args()` 移除 `--notify-grace-seconds`。保留 `DeploymentPolicy` 和 `classify_deployment()`，因为 Notify Pool 迁移及旧 Pool 队列诊断仍依赖它们。

- [ ] **Step 4: 运行聚焦测试并确认通过**

Run:

```powershell
python -m pytest -q tests/test_prefect_startup_reconcile.py
```

Expected: PASS；过期自动、过期手动和过期 Dashboard 单例均记录为 `startup_overdue_run`，未来与无计划时间 Run 未取消。

- [ ] **Step 5: 提交任务 1**

```powershell
git add scripts/lib/prefect_startup_reconcile.py tests/test_prefect_startup_reconcile.py
git commit -m "fix: clear overdue Prefect runs at startup"
```

### Task 2: 删除已废弃的 Notify 宽限期运行配置

**Files:**
- Modify: `scripts/lib/runtime_config.ps1:80-93`
- Modify: `scripts/run.ps1:176-185`
- Modify: `config/runtime.local.example.json:14-26`
- Modify: `tests/test_development_environment_contract.py:145-160, 205-220, 298-390`

**Interfaces:**
- Consumes: `config/runtime.local.json` 的 `runtime.root` 和三个 `runtime.work_pools` 配置。
- Produces: 运行时环境不再导出 `AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS`，启动清理 CLI 只接收 `--notify-work-pool` 和可选 `--cancel-in-flight`。

- [ ] **Step 1: 写入失败的运行配置契约测试**

调整 `test_runtime_json_template_exports_three_pool_topology_and_runtime_root()`，断言 `scheduled_notify_grace_seconds` 不在 `runtime` 中。调整环境变量契约测试，断言 `AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS` 不再出现在 `runtime_config.ps1` 导出的环境变量集合及 `scripts/run.ps1` 的 reconcile 参数中。

```python
assert "scheduled_notify_grace_seconds" not in runtime
assert "AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS" not in loader_source
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest -q tests/test_development_environment_contract.py -k "runtime_json_template or scheduled_notify"
```

Expected: FAIL，因为当前示例 JSON 和 PowerShell 仍声明 Notify 宽限期。

- [ ] **Step 3: 删除宽限期配置读取和调用参数**

从 `runtime_config.ps1` 删除：

```powershell
$env:AUTO_NOTIFY_SCHEDULED_NOTIFY_GRACE_SECONDS = Get-RuntimeConfigPositiveInt -Config $config -Path "runtime.scheduled_notify_grace_seconds"
```

从 `config/runtime.local.example.json` 的 `runtime` 对象删除 `scheduled_notify_grace_seconds`。从 `scripts/run.ps1` 的 `$ReconcileArgs` 中删除 `--notify-grace-seconds` 及环境变量值，仅保留：

```powershell
$ReconcileArgs = @(
    "--notify-work-pool",
    $env:PREFECT_NOTIFY_POOL_NAME
)
```

不修改用户本机已忽略的 `config/runtime.local.json`；多余 JSON 字段在删除读取后不会影响启动。

- [ ] **Step 4: 运行配置契约测试并确认通过**

Run:

```powershell
python -m pytest -q tests/test_development_environment_contract.py -k "runtime_json_template or scheduled_notify"
```

Expected: PASS；模板和环境加载器都不再要求 Notify 宽限期。

- [ ] **Step 5: 提交任务 2**

```powershell
git add scripts/lib/runtime_config.ps1 scripts/run.ps1 config/runtime.local.example.json tests/test_development_environment_contract.py
git commit -m "refactor: remove Prefect startup grace setting"
```

### Task 3: 固化首次 Session Keeper 的启动顺序并更新运维文档

**Files:**
- Modify: `scripts/run.ps1:195-240`
- Modify: `tests/test_development_environment_contract.py:518-555`
- Modify: `README.md:160-190`
- Modify: `PROJECT_GUIDE.md:186-205`

**Interfaces:**
- Consumes: 已完成的启动清理和 `PREFECT_SESSION_POOL_NAME` 环境变量。
- Produces: 先启动并确认 Session Worker，唯一一次提交 `session-keeper-flow/session-keeper`，再启动 Dashboard 和 Notify Worker。

- [ ] **Step 1: 写入失败的启动顺序测试**

在 `test_runtime_start_queues_initial_session_keeper_run_after_worker_start()` 中新增 Dashboard 与 Notify Worker 标记，并断言首次 Session Keeper 命令处在二者之前：

```python
dashboard_worker = "-WorkPool $env:PREFECT_DASHBOARD_POOL_NAME"
notify_worker = "-WorkPool $env:PREFECT_NOTIFY_POOL_NAME"

assert source.index(online_wait) < source.index(keeper_command)
assert source.index(keeper_command) < source.index(dashboard_worker)
assert source.index(keeper_command) < source.index(notify_worker)
```

同时保留 `source.count(keeper_command) == 1`，确保启动时只提交一条新的 Session Keeper Run。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
python -m pytest -q tests/test_development_environment_contract.py::test_runtime_start_queues_initial_session_keeper_run_after_worker_start
```

Expected: FAIL，因为当前 Dashboard 和 Notify Worker 先于新的 Session Keeper 命令启动。

- [ ] **Step 3: 调整 Worker 启动顺序并说明策略**

在 `scripts/run.ps1` 中保留清理发生在全部 Worker 前；启动 Session Worker、等待在线、提交一次 Session Keeper 后，再执行已有的 Dashboard Worker 和 Notify Worker 启动块。不要等待 Session Keeper Flow 完成，以免登录检查阻塞整个服务启动。

在 `README.md` 的运行配置章节说明：启动时会取消所有已有的过期排队 Run，随后提交一次新的 Session Keeper；运行中 Run 仍要求 `-ForceRestart` 才会取消。`PROJECT_GUIDE.md` 添加 2026-07-17 变更记录，注明统一过期清理、取消 Dashboard 补跑和移除 Notify 宽限期配置。

- [ ] **Step 4: 运行启动契约与文档检查**

Run:

```powershell
python -m pytest -q tests/test_development_environment_contract.py -k "runtime_start_queues_initial_session_keeper_run or run_script_reconciles"
git diff --check
```

Expected: PASS；Session Keeper 命令只出现一次，并位于 Dashboard/Notify Worker 启动块之前。

- [ ] **Step 5: 提交任务 3**

```powershell
git add scripts/run.ps1 tests/test_development_environment_contract.py README.md PROJECT_GUIDE.md
git commit -m "feat: clear stale Prefect runs before startup"
```

### Task 4: 集成验证

**Files:**
- Verify only: `scripts/lib/prefect_startup_reconcile.py`, `scripts/run.ps1`, `scripts/lib/runtime_config.ps1`, `tests/`, `README.md`, `PROJECT_GUIDE.md`

**Interfaces:**
- Consumes: 三个已提交任务的代码和测试。
- Produces: 可推送的干净工作树及可复现的验证记录。

- [ ] **Step 1: 运行启动清理与配置聚焦测试**

Run:

```powershell
python -m pytest -q tests/test_prefect_startup_reconcile.py tests/test_development_environment_contract.py
```

Expected: PASS。

- [ ] **Step 2: 运行完整质量检查**

Run:

```powershell
ruff check backend infrastructure models services tasks flows tests migrations
python -m pytest -q
git diff --check
git status --short --branch
```

Expected: Ruff 无错误、pytest 全部通过、`git diff --check` 无输出，工作树除已提交内容外干净。

- [ ] **Step 3: 提交最终验证状态**

如果验证没有产生新文件，不创建空提交；报告各命令结果及提交列表。
