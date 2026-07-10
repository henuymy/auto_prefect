# 驾驶舱 V2 仓库可靠性完善设计

## 目标

完善三个已经确认的问题：

1. MySQL 命名锁心跳失败或锁所有权丢失后，采集不得继续提交业务事务。
2. 候选结构中稳定存在的节点，每次可信采集都刷新 `last_seen_at`。
3. readiness 必须验证足以支撑仓库正确性的关键结构，而不只是表名和少量约束。

本次不改变采集口径、结构漂移算法、指标计算方式、数据保留天数或历史查询语义。

## 一、MySQL 命名锁失败关闭

### 锁租约对象

`dashboard_mysql_lock()` 不再返回普通字典，而是返回 `DashboardMySQLLockLease`。租约保存：

- 锁名称和数据库后端；
- 获取锁的 MySQL `CONNECTION_ID()`；
- 心跳停止事件；
- 心跳失败或所有权丢失状态；
- 串行访问锁连接的线程互斥锁。

租约提供：

```python
lease.assert_held()
lease.as_dict()
```

`assert_held()` 先检查心跳失败状态，再在互斥锁内执行 `IS_USED_LOCK(lock_name)`，只有结果等于获取锁时记录的连接 ID 才通过。连接异常、返回 `NULL`、返回其他连接 ID 都抛出 `DashboardMySQLLockLostError`。

非 MySQL 测试后端使用 no-op 租约，保持 SQLite 单元测试兼容。

### 心跳

心跳线程与主线程不能同时使用同一 SQLAlchemy Connection。所有心跳、同步所有权检查和释放操作都通过同一个线程互斥锁。

心跳周期内执行所有权检查；出现 SQLAlchemy 异常或所有权不匹配时：

1. 保存失败类型；
2. 设置锁丢失状态；
3. 退出心跳线程；
4. 不在后台线程直接操作业务 Session。

### 提交边界

三个写入入口必须在最终业务事务提交前调用 `lease.assert_held()`：

- V1 `dashboard_pipeline`：调用后才执行 `transaction.commit()`；
- V2 `dashboard_v2_pipeline`：在 `session.begin()` 即将退出前调用；
- V2 指标同步：在同步结果和批次成功状态写入后、事务退出前调用。

检查失败时业务 Session 回滚，外层既有失败收口逻辑把批次标记为 `FAILED`。错误类型使用 `DASHBOARD_MYSQL_LOCK_LOST`，错误消息不包含数据库凭据。

业务已经成功提交后，如果只有 `RELEASE_LOCK()` 回执失败，仍保留成功状态并记录告警。释放失败不能反向修改已提交数据。

对外返回的 `lock` 元数据继续是可 JSON 序列化字典，由 `lease.as_dict()` 生成，不暴露 Connection 对象。

## 二、稳定节点刷新 `last_seen_at`

`sync_v2_hierarchy_in_session()` 对候选图中的每个既有节点执行以下规则：

```text
last_seen_at = 本批次 collected_at
missing_count = 0
enabled = True
```

更新时间不再依赖名称、父级或启用状态是否变化。

统计口径保持原样：

- `updated` 只统计业务字段变化；
- 单纯刷新 `last_seen_at` 不增加 `updated`；
- `restored`、`moved`、`missing_incremented` 和 `disabled` 维持现有含义。

节点缺失时的 `last_seen_at` 不表示“最后一次看到”，因此缺失计数增加但尚未停用时不更新；达到阈值停用时也不把停用时间写入 `last_seen_at`。停用发生时间可由批次和父级历史追踪，`last_seen_at` 始终表示最后一次可信观测时间。

## 三、readiness 声明式结构验证

### Alembic head

期望 revision 从 `alembic_dashboard_v2.ini` 对应的迁移链动态读取 current head。保留 `EXPECTED_REVISION` 导出，供现有初始化和导入脚本使用，但不再手工硬编码。

如果迁移链不存在多个 head、无法读取配置或数据库 `alembic_version` 不匹配，readiness 失败并返回明确原因。

### 关键结构清单

readiness 使用集中声明的 schema manifest，至少检查：

- 10 张业务表和每张表的关键列；
- `hierarchy_parent_history.active_child_node_id` 为持久化生成列；
- 所有业务幂等唯一约束：批次号、节点编码、当前父级、指标编码、公式组件、目标方案、目标值、current、snapshot 和 acc；
- 关键外键的目标表及删除策略，包括 `RESTRICT`、`SET NULL` 和 `CASCADE`；
- 快照复合主键严格为 `(id, collected_at)`，且分区表没有外键；
- 采集、历史和排序所依赖的关键索引。

CHECK 约束在目标 MySQL 8.4 上也纳入验证；若 SQLAlchemy inspector 无法稳定返回表达式，则通过 `information_schema.TABLE_CONSTRAINTS` 和 `CHECK_CONSTRAINTS` 读取约束名，不比较格式化后的 SQL 文本。

### 分区覆盖

readiness 查询 `information_schema.PARTITIONS`，要求：

- 存在 `p_future`；
- 从上海业务日期当天到未来第 30 天，每天都有 `pYYYYMMDD` 分区；
- 分区名称和边界与日期一致。

`check_dashboard_v2_schema()` 增加可选 `now` 参数，仅用于确定性测试；生产默认使用上海时区当前时间。

### 返回结果

返回值保留 `ok/message/expected_revision/actual_revision`，并新增可定位字段：

```json
{
  "missing_tables": [],
  "missing_columns": {},
  "missing_unique_constraints": [],
  "missing_foreign_keys": [],
  "missing_check_constraints": [],
  "missing_indexes": [],
  "generated_columns_ok": true,
  "metric_snapshot_primary_key_ok": true,
  "metric_snapshot_has_no_foreign_keys": true,
  "missing_snapshot_partitions": [],
  "metric_snapshot_partition_ok": true
}
```

任一关键项失败都令 `ok=False`，从而让 `/api/health` 返回 503。

## 四、测试策略

### 锁

- 单元测试获取锁时记录连接 ID，并验证 `as_dict()` 可序列化。
- 模拟心跳 SQLAlchemy 异常，验证租约进入丢失状态。
- 模拟 `IS_USED_LOCK()` 返回 `NULL` 或其他连接 ID，验证 `assert_held()` 抛出专用异常。
- 验证 V1、V2、指标同步都在提交前调用租约检查；检查失败时事务不提交。
- 保留“提交后释放回执失败只告警”的现有测试。
- 真实 MySQL 集成测试验证另一连接无法获取锁，并验证主动关闭锁连接后租约检查失败。

### 层级

- 新增 SQLite Session 测试：稳定节点字段完全不变时，`last_seen_at` 更新、`missing_count` 清零、`updated == 0`。
- 验证缺失但未停用节点不刷新 `last_seen_at`。
- 验证恢复节点刷新时间并维持既有 `restored` 统计。

### readiness

- 将结构检查核心拆成可注入 Connection/Inspector 的纯检查单元，逐项测试缺少唯一约束、外键、生成列、索引、CHECK 和未来分区时失败。
- 真实 MySQL 集成测试从空库升级、维护未来分区后 readiness 通过。
- 在一次性测试库删除一个关键索引后 readiness 必须失败，恢复迁移后再次通过。

## 五、验收标准

1. 心跳失败或锁所有权丢失后，三个写入入口均不能提交业务事务。
2. 锁正常时原有采集、指标同步和返回 DTO 保持兼容。
3. 稳定节点每次可信采集刷新 `last_seen_at`，但不被统计为结构字段更新。
4. readiness 能明确发现关键唯一约束、外键、生成列、索引、CHECK 或未来分区缺失。
5. Alembic 期望 head 不再依赖手工同步常量。
6. 完整 pytest、Ruff 和可用的真实 MySQL 集成测试通过。
