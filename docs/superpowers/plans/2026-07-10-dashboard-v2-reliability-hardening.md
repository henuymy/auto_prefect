# 驾驶舱 V2 仓库可靠性完善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 确保 MySQL 命名锁丢失时业务事务失败关闭、稳定节点刷新最近观测时间，并让 readiness 完整验证仓库关键结构。

**Architecture:** 命名锁改为可同步验证所有权的租约对象，三个写入入口在提交前执行租约检查。层级同步把观测时间与结构变化统计分离。readiness 将数据库反射结果归一化为结构快照，再用声明式 manifest 评估 Alembic head、列、约束、索引和分区覆盖。

**Tech Stack:** Python 3.11+、SQLAlchemy 2.x、PyMySQL 1.1+、Alembic 1.13+、MySQL 8.4、pytest 8、Ruff

## Global Constraints

- 不改变采集口径、结构漂移算法、指标计算方式、数据保留天数或历史查询语义。
- 提交前锁丢失必须回滚；业务提交后的 `RELEASE_LOCK()` 回执失败只记录告警。
- `last_seen_at` 表示最后一次可信观测时间，缺失或停用动作不得把它改成处理时间。
- readiness 的期望 revision 必须来自 `alembic_dashboard_v2.ini` 对应迁移链 current head。
- MySQL 特有的锁、生成列、外键和分区行为必须保留真实 MySQL 集成测试。

---

### Task 1: 引入可验证的 MySQL 锁租约

**Files:**
- Modify: `infrastructure/dashboard_mysql.py:1-239`
- Modify: `tests/test_dashboard_mysql.py:138-239`
- Modify: `tests/test_dashboard_mysql_integration.py:116-124`

**Interfaces:**
- Produces: `DashboardMySQLLockLostError(RuntimeError)`，`error_type = "DASHBOARD_MYSQL_LOCK_LOST"`
- Produces: `DashboardMySQLLockLease.assert_held() -> None`
- Produces: `DashboardMySQLLockLease.as_dict() -> dict[str, object]`
- Changes: `dashboard_mysql_lock(...) -> Iterator[DashboardMySQLLockLease]`

- [ ] **Step 1: 写入锁所有权丢失的失败测试**

在 `tests/test_dashboard_mysql.py` 增加一个可控制标量返回值的 FakeConnection，并覆盖：

```python
def test_dashboard_mysql_lock_lease_rejects_lost_owner():
    connection = FakeLockConnection(
        get_lock=1,
        connection_id=41,
        owners=[99],
        release=1,
    )
    with dashboard_mysql.dashboard_mysql_lock(
        FakeLockEngine(connection), heartbeat_seconds=0
    ) as lease:
        with pytest.raises(
            dashboard_mysql.DashboardMySQLLockLostError,
            match="所有权已丢失",
        ):
            lease.assert_held()
```

再增加 `IS_USED_LOCK()` 抛出 `SQLAlchemyError` 时抛专用异常，以及 `as_dict()` 只包含 `name/backend/connection_id` 的测试。

- [ ] **Step 2: 运行测试并确认租约接口尚不存在**

Run:

```powershell
python -m pytest tests/test_dashboard_mysql.py -q
```

Expected: FAIL，失败原因是 `DashboardMySQLLockLostError` 或 `lease.assert_held` 不存在。

- [ ] **Step 3: 实现专用异常和锁租约**

在 `infrastructure/dashboard_mysql.py` 增加：

```python
class DashboardMySQLLockLostError(RuntimeError):
    error_type = "DASHBOARD_MYSQL_LOCK_LOST"


class DashboardMySQLLockLease:
    def __init__(self, *, connection, lock_name, backend, connection_id=None):
        self._connection = connection
        self.name = lock_name
        self.backend = backend
        self.connection_id = connection_id
        self._connection_guard = Lock()
        self._lost = Event()
        self._loss_type: str | None = None

    def mark_lost(self, reason: object) -> None:
        self._loss_type = type(reason).__name__ if not isinstance(reason, str) else reason
        self._lost.set()

    def assert_held(self) -> None:
        if self.backend not in {"mysql", "mariadb"}:
            return
        if self._lost.is_set():
            raise DashboardMySQLLockLostError(
                f"MySQL 驾驶舱采集锁已丢失: {self.name} ({self._loss_type})"
            )
        try:
            with self._connection_guard:
                owner = self._connection.scalar(
                    text("SELECT IS_USED_LOCK(:lock_name)"),
                    {"lock_name": self.name},
                )
        except SQLAlchemyError as exc:
            self.mark_lost(exc)
            raise DashboardMySQLLockLostError(
                f"MySQL 驾驶舱采集锁校验失败: {self.name} ({type(exc).__name__})"
            ) from exc
        if owner != self.connection_id:
            self.mark_lost("OWNER_MISMATCH")
            raise DashboardMySQLLockLostError(
                f"MySQL 驾驶舱采集锁所有权已丢失: {self.name}"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "backend": self.backend,
            "connection_id": self.connection_id,
        }
```

用私有 `_query_owner()` 封装 `IS_USED_LOCK()` 查询，让心跳和同步检查都调用该方法；公共接口名称和错误类型保持不变。

- [ ] **Step 4: 修改 contextmanager 获取连接 ID 并让心跳验证所有权**

获取 `GET_LOCK()` 成功后执行：

```python
connection_id = connection.scalar(text("SELECT CONNECTION_ID()"))
lease = DashboardMySQLLockLease(
    connection=connection,
    lock_name=normalized_name,
    backend=engine.dialect.name,
    connection_id=int(connection_id),
)
```

心跳循环调用 `lease.assert_held()`；捕获 `DashboardMySQLLockLostError` 后记录告警并退出。`RELEASE_LOCK()` 也在 `lease._connection_guard` 内执行。非 MySQL 分支 yield 一个 no-op lease。

- [ ] **Step 5: 运行锁单元测试**

Run:

```powershell
python -m pytest tests/test_dashboard_mysql.py -q
```

Expected: 全部通过，现有释放失败测试仍证明提交后只告警。

- [ ] **Step 6: 扩展真实 MySQL 锁测试**

在 `tests/test_dashboard_mysql_integration.py` 的命名锁测试中，进入上下文后调用：

```python
lease.assert_held()
assert lease.connection_id is not None
```

并保留第二连接无法获取同名锁的断言。

- [ ] **Step 7: 提交租约实现**

```powershell
git add infrastructure/dashboard_mysql.py tests/test_dashboard_mysql.py tests/test_dashboard_mysql_integration.py
git commit -m "fix: 命名锁丢失时失败关闭"
```

### Task 2: 在三个业务提交点验证锁租约

**Files:**
- Modify: `services/dashboard_batch_runner.py:44-56,218-233`
- Modify: `services/dashboard_pipeline.py:285-298`
- Modify: `services/dashboard_v2_batch_runner.py:36-48,223-237`
- Modify: `services/dashboard_v2_pipeline.py:221-304`
- Modify: `services/dashboard_v2_indicator_sync.py:86-145`
- Modify: `tests/test_dashboard_pipeline.py`
- Modify: `tests/test_dashboard_v2_pipeline.py`
- Create: `tests/test_dashboard_v2_indicator_sync.py`

**Interfaces:**
- Consumes: `DashboardMySQLLockLease` from Task 1
- Adds: `DashboardBatchContext.database_lock`
- Adds: `DashboardV2BatchContext.database_lock`

- [ ] **Step 1: 写入三个提交门的失败测试**

为每个入口使用记录调用的假租约：

```python
class RecordingLease:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def assert_held(self):
        self.calls += 1
        if self.error:
            raise self.error

    def as_dict(self):
        return {"name": "test", "backend": "mysql", "connection_id": 1}
```

测试要求：

- V1 成功路径 `calls == 1` 且检查发生在 `transaction.commit()` 前；
- V2 `_write_v2_transaction` 检查失败时 `session.begin()` 回滚，批次不能完成提交；
- 指标同步检查失败时外层把批次写成 `FAILED`，不能返回成功结果。

- [ ] **Step 2: 运行三个测试文件并确认失败**

Run:

```powershell
python -m pytest tests/test_dashboard_pipeline.py tests/test_dashboard_v2_pipeline.py tests/test_dashboard_v2_indicator_sync.py -q
```

Expected: FAIL，原因是批次上下文没有 `database_lock` 或提交前未调用 `assert_held()`。

- [ ] **Step 3: 给批次上下文传递租约并保留字典 DTO**

两个 batch dataclass 增加：

```python
database_lock: DashboardMySQLLockLease
```

构造上下文时使用：

```python
database_lock=database_lock,
lock_result={**local_lock, "database_lock": database_lock.as_dict()},
```

- [ ] **Step 4: 在业务提交前加入强校验**

V1 在 `transaction.commit()` 前加入：

```python
batch.database_lock.assert_held()
transaction.commit()
```

V2 在 `finalize_v2_run_in_session(...)` 后、`session.begin()` 退出前加入：

```python
batch.database_lock.assert_held()
```

指标同步在设置 `run.status = "SUCCESS"` 等字段后、事务退出前加入：

```python
database_lock.assert_held()
```

返回结果中的锁信息调用 `database_lock.as_dict()`。

- [ ] **Step 5: 运行提交门测试并确认通过**

Run:

```powershell
python -m pytest tests/test_dashboard_pipeline.py tests/test_dashboard_v2_pipeline.py tests/test_dashboard_v2_indicator_sync.py -q
```

Expected: 全部通过。

- [ ] **Step 6: 提交业务集成**

```powershell
git add services/dashboard_batch_runner.py services/dashboard_pipeline.py services/dashboard_v2_batch_runner.py services/dashboard_v2_pipeline.py services/dashboard_v2_indicator_sync.py tests/test_dashboard_pipeline.py tests/test_dashboard_v2_pipeline.py tests/test_dashboard_v2_indicator_sync.py
git commit -m "fix: 提交采集事务前验证数据库锁"
```

### Task 3: 刷新稳定节点最后观测时间

**Files:**
- Create: `tests/test_dashboard_v2_hierarchy_sync.py`
- Modify: `services/dashboard_v2_hierarchy.py:492-541`

**Interfaces:**
- Consumes: `sync_v2_hierarchy_in_session(...)`
- Preserves: 返回统计字典的现有键和值语义

- [ ] **Step 1: 建立最小 SQLite 层级表测试夹具**

新测试文件用原生 SQL 创建 ORM 所需的 `collection_run`、`hierarchy_node` 和 `hierarchy_parent_history` 列。插入 CITY→BRANCH 当前关系，并构造字段完全一致的候选图。

- [ ] **Step 2: 写入稳定节点失败测试**

```python
def test_stable_observed_nodes_refresh_last_seen_without_structure_update(session):
    observed_at = datetime(2026, 7, 10, 10, 0)
    result = sync_v2_hierarchy_in_session(
        session,
        stable_graph(),
        collected_at=observed_at,
        collection_run_id=None,
    )
    branch = session.scalar(
        select(HierarchyNode).where(HierarchyNode.node_code == "B")
    )

    assert branch.last_seen_at == observed_at
    assert branch.missing_count == 0
    assert result["updated"] == 0
```

再增加缺失节点 `missing_count` 增加但 `last_seen_at` 保持最后观测时间的测试。

- [ ] **Step 3: 运行测试并确认稳定节点时间未刷新**

Run:

```powershell
python -m pytest tests/test_dashboard_v2_hierarchy_sync.py -q
```

Expected: FAIL，稳定节点仍保留旧 `last_seen_at`；现有代码还会在停用时错误刷新时间。

- [ ] **Step 4: 分离观测时间与业务字段变化**

在既有节点分支中，无条件执行：

```python
node.last_seen_at = collected_at
```

保留 `updated += 1` 只受 `changed` 控制。删除缺失停用分支中的：

```python
node.last_seen_at = collected_at
```

- [ ] **Step 5: 运行层级测试**

Run:

```powershell
python -m pytest tests/test_dashboard_v2_hierarchy.py tests/test_dashboard_v2_hierarchy_sync.py -q
```

Expected: 全部通过。

- [ ] **Step 6: 提交层级修复**

```powershell
git add services/dashboard_v2_hierarchy.py tests/test_dashboard_v2_hierarchy_sync.py
git commit -m "fix: 刷新稳定节点最后观测时间"
```

### Task 4: 建立声明式 readiness 结构快照

**Files:**
- Create: `tests/test_dashboard_v2_readiness.py`
- Modify: `services/dashboard_v2_readiness.py`
- Modify: `tests/test_dashboard_v2_mysql_integration.py:385-416`

**Interfaces:**
- Produces: `DashboardV2SchemaSnapshot`
- Produces: `collect_dashboard_v2_schema(connection) -> DashboardV2SchemaSnapshot`
- Produces: `evaluate_dashboard_v2_schema(snapshot, *, expected_revision, now) -> dict[str, Any]`
- Changes: `check_dashboard_v2_schema(*, now: datetime | None = None) -> dict[str, Any]`
- Preserves: exported `EXPECTED_REVISION: str`

- [ ] **Step 1: 写入动态 Alembic head 测试**

```python
def test_expected_revision_matches_migration_head():
    config = Config(str(PROJECT_ROOT / "alembic_dashboard_v2.ini"))
    assert EXPECTED_REVISION == ScriptDirectory.from_config(config).get_current_head()
```

- [ ] **Step 2: 写入声明式评估失败测试**

构建一个 `valid_snapshot()`，然后逐项删除：

```python
@pytest.mark.parametrize(
    ("mutation", "result_key"),
    [
        (drop_active_parent_unique, "missing_unique_constraints"),
        (drop_current_node_fk, "missing_foreign_keys"),
        (drop_active_child_computed, "generated_columns_ok"),
        (drop_snapshot_history_index, "missing_indexes"),
        (drop_status_check, "missing_check_constraints"),
        (drop_tomorrow_partition, "missing_snapshot_partitions"),
    ],
)
def test_readiness_rejects_missing_critical_schema(mutation, result_key):
    snapshot = valid_snapshot()
    mutation(snapshot)
    result = evaluate_dashboard_v2_schema(
        snapshot,
        expected_revision="head",
        now=datetime(2026, 7, 10, 12),
    )
    assert result["ok"] is False
    assert result[result_key]
```

- [ ] **Step 3: 运行 readiness 测试并确认新接口不存在**

Run:

```powershell
python -m pytest tests/test_dashboard_v2_readiness.py -q
```

Expected: FAIL，原因是 snapshot/evaluate 接口尚不存在或 revision 仍硬编码。

- [ ] **Step 4: 动态加载迁移 head**

使用项目绝对路径配置：

```python
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_expected_revision() -> str:
    config = Config(str(PROJECT_ROOT / "alembic_dashboard_v2.ini"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if not head:
        raise RuntimeError("驾驶舱 V2 迁移链没有唯一 head")
    return head


EXPECTED_REVISION = load_expected_revision()
```

- [ ] **Step 5: 定义结构快照和 manifest**

`DashboardV2SchemaSnapshot` 至少保存：表、各表列、生成列、唯一约束、外键及 ondelete、CHECK 名、索引列、主键、snapshot 分区名称和边界、数据库 revision。

Manifest 必须覆盖设计规格中列出的 10 张表、全部业务唯一约束、关键外键、生成列、CHECK 和查询索引。约束比较使用归一化元组，不比较数据库返回顺序之外的 DDL 文本格式。

- [ ] **Step 6: 实现评估器及详细错误返回**

`evaluate_dashboard_v2_schema` 计算上海业务日期当天到第 30 天的：

```python
required_partitions = {
    f"p{(business_day + timedelta(days=offset)):%Y%m%d}"
    for offset in range(31)
}
```

同时验证每个日分区的 `PARTITION_DESCRIPTION` 等于下一天日期，并要求 `p_future`。任一缺失项令 `ok=False`。

- [ ] **Step 7: 实现数据库结构采集和公共入口**

`collect_dashboard_v2_schema` 使用 SQLAlchemy inspector 收集常规结构；CHECK 和分区边界使用 `information_schema`。`check_dashboard_v2_schema(now=None)` 负责创建/释放 engine、捕获 `SQLAlchemyError`，然后调用评估器。

- [ ] **Step 8: 运行 readiness 单元测试**

Run:

```powershell
python -m pytest tests/test_dashboard_v2_readiness.py tests/test_health_service.py -q
```

Expected: 全部通过。

- [ ] **Step 9: 更新真实 MySQL 集成测试的确定性时间**

维护分区后调用：

```python
assert check_dashboard_v2_schema(now=datetime(2026, 6, 30, 12))["ok"] is True
```

在一次性测试库中删除一个非唯一关键索引，断言 readiness 返回对应 `missing_indexes`；测试结束前按迁移定义重建索引，避免污染后续 downgrade/upgrade 测试。

- [ ] **Step 10: 提交 readiness 完善**

```powershell
git add services/dashboard_v2_readiness.py tests/test_dashboard_v2_readiness.py tests/test_dashboard_v2_mysql_integration.py
git commit -m "feat: 完整校验驾驶舱仓库结构"
```

### Task 5: 完整验证与目标审计

**Files:**
- Verify: all files changed by Tasks 1-4
- Verify: `docs/superpowers/specs/2026-07-10-dashboard-v2-reliability-hardening-design.md`

**Interfaces:**
- Consumes: all preceding task outputs
- Produces: completion evidence for every objective item

- [ ] **Step 1: 运行完整 Python 测试**

```powershell
python -m pytest -q
```

Expected: 无失败；未配置 `DASHBOARD_TEST_MYSQL_URL` 时 MySQL 集成测试按既有 marker 跳过。

- [ ] **Step 2: 在配置了测试库时运行真实 MySQL 集成测试**

```powershell
python -m pytest -m mysql_integration tests/test_dashboard_mysql_integration.py tests/test_dashboard_v2_mysql_integration.py -q
```

Expected: 全部通过；如果环境未配置测试库，报告为未验证项，不能声称真实 MySQL 行为已在本机证明。

- [ ] **Step 3: 运行 Ruff**

```powershell
python -m ruff check .
```

Expected: `All checks passed!`。

- [ ] **Step 4: 检查差异和工作树**

```powershell
git diff --check
git status --short
git log -8 --oneline
```

Expected: 无未提交修改，最近提交分别对应锁租约、提交门、last_seen_at 和 readiness。

- [ ] **Step 5: 逐项审计目标证据**

确认：

```text
锁租约单元测试证明心跳/所有权丢失抛专用异常
三个业务提交门测试证明检查发生在提交前
稳定节点测试证明时间刷新且 updated 不增加
readiness 变异测试证明每类关键结构缺失都会失败
动态 Alembic head 测试证明不再手工硬编码
```
