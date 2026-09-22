# 驾驶舱 MySQL 连接与锁等待故障处置记录

## 现象

驾驶舱 `REALTIME:session` 采集曾出现以下错误：

- `2013 Lost connection to MySQL server during query`；
- `1205 Lock wait timeout exceeded`；
- 上游监控出现 `RECONCILIATION_FAILED`。

涉及的典型 SQL 包括层级节点读取，以及对 `collection_run` 执行 `FOR UPDATE` 的超时批次回收。

## 原因判断

### 人工重启导致的连接断开

MySQL 日志中的两次 `Received SHUTDOWN` 是人工重启产生的，不是 mysqld 自行崩溃。重启期间 MySQL 会强制关闭 `dashboard_app` 连接，正在执行的查询因此可能返回 `2013`。重启后应用连接池中的旧连接也可能继续报连接失效，应重启 Dashboard Worker 或显式销毁连接池。

### `collection_run` 行锁竞争

实时采集启动前会回收长时间处于 `PENDING/RUNNING` 的批次，原逻辑使用整批 `SELECT ... FOR UPDATE`。分区/保留维护也会更新 `collection_run`，两类事务可能互相等待，最终触发 `1205`。这属于应用事务并发问题，不应只通过增大 `innodb_lock_wait_timeout` 掩盖。

### IO 与 redo log 压力

日志中还出现过 IO-layer timeout 和 redo log checkpoint lag 告警。它们提示需要检查 Docker 容器的 CPU、内存、磁盘空间和磁盘延迟；只有确认资源充足后，才评估增加 `innodb_redo_log_capacity`。

## 已实施的代码修复

### 超时批次回收改为非阻塞

`services/dashboard_v2_batch_runner.py` 的 `recover_stale_v2_runs()` 现在：

1. MySQL/MariaDB 使用 `FOR UPDATE SKIP LOCKED`，跳过当前被其他事务占用的行；
2. 对 MySQL `1205/1213` 做最多三次短退避重试；
3. 持续冲突时延期本次回收并返回 `0`，不阻断实时采集；
4. 遇到 `2006/2013` 时销毁连接池并延期回收，下一批次再处理。

SQLite 测试环境不启用 `SKIP LOCKED`，保持兼容。

### 维护任务与采集任务统一数据库锁

`tasks/dashboard_maintenance_tasks.py` 优先使用 `collection_database_lock_name`，使分区/保留维护与实时采集互斥，避免维护事务在采集启动时持有 `collection_run` 行锁。

### 缩小层级查询返回字段

`services/dashboard_v2_hierarchy.py` 的结构图读取只投影节点身份、父级和请求/指标开关所需的 7 个字段，减少每次采集的传输量和 ORM 开销。

## 运维检查

只读诊断命令：

```sql
SHOW FULL PROCESSLIST;
SHOW ENGINE INNODB STATUS;
SELECT * FROM sys.innodb_lock_waits;
SELECT * FROM information_schema.innodb_trx;
```

如果 `sys.innodb_lock_waits` 不存在，可改用：

```sql
SELECT * FROM performance_schema.data_lock_waits;
```

重点关注长事务、`Waiting for ... lock` 状态、`collection_run` 相关 SQL，以及 `dashboard_app` 连接。不要在未确认业务影响前直接执行 `KILL`。

## 发布与验证

1. 部署本次代码；
2. 重启 Dashboard Worker/后端，重建 SQLAlchemy 连接池；
3. 确认 MySQL 已稳定运行后手动执行一次 `REALTIME:session`；
4. 检查 Flow 为 `SUCCESS`，日志不再出现 `1205/1213`；
5. 继续观察 MySQL 日志是否还有 `2013`、IO-layer timeout 或 redo log checkpoint lag。

## 测试结果

本次修复相关专项测试通过，锁、MySQL 适配与保留维护测试通过；未配置真实 MySQL 的集成测试按项目配置被跳过。完整测试集中存在与本次数据库修复无关的前端契约、监控流、Prefect 配置和测试 fixture 失败，发布前应单独处理。
