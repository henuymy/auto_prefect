# Dashboard 当前版本说明

本文只描述当前有效实现，历史推演和修复过程见：

- `docs/dashboard收缩流程修复记录.md`
- `docs/dashboard完整收缩流程图.md`
- `docs/dashboard数据库流程与表结构.md`

## 1. 当前采集边界

请求目标只允许：

```text
CITY
BRANCH
GRID
CHANNEL_MANAGER
```

明确不请求：

```text
CHANNEL
```

渠道指标来自：

```text
CHANNEL_MANAGER 响应中的 CHANNEL 行
```

结构关系来自：

```text
GRID -> CHANNEL_MANAGER
CHANNEL_MANAGER -> CHANNEL
```

## 2. 当前核心表

```text
area
  CITY / BRANCH / GRID / CHANNEL

request_target
  CITY / BRANCH / GRID / CHANNEL_MANAGER

channel_manager_area
  CHANNEL_MANAGER -> CHANNEL

collection_run
  批次状态、计数、结构变化摘要

metric_current / metric_snapshot / metric_acc
  指标数据
```

`collection_run.structure_change_summary` 保存本批次结构变化摘要 JSON。

## 3. 主流程

```text
1. 加载 area / request_target / channel_manager_area 当前结构
2. 并发请求 request_target
3. 解析 rows 和 structure_observations
4. 构建 observed_graph
5. 对比 current_graph 和 observed_graph
6. 校验区域覆盖和指标值
7. 如发现结构变化，按策略重采
8. 最终事务写入结构、指标、结构变化摘要
```

最终事务写入顺序：

```text
area
request_target
channel_manager_area
collection_run.structure_change_summary
metric_current / metric_snapshot / metric_acc
collection_run = SUCCESS
```

## 4. 重采策略

配置：

```json
"collection_retry_strategy": "affected_grid"
```

支持：

```text
affected_grid
  默认值；
  只重采受影响 GRID 子树；
  适合日常渠道变化少的场景。

full
  第二轮重采完整候选目标；
  适合低峰期、结构大调整、或人工校准。
```

## 5. 关系缺失容忍阈值

配置：

```json
"relation_missing_disable_threshold": 2
```

含义：

```text
普通关系缺失：
  第一次缺失 -> missing_count + 1，不禁用；
  连续达到阈值 -> enabled = false。

manager 被确认删除：
  立即禁用该 manager 下关系。

CHANNEL 同轮已转移到另一个 manager：
  立即禁用旧 manager 关系，启用新 manager 关系。
```

这样可以降低平台偶发漏返回导致的误禁用，同时不影响渠道转移的及时校准。

## 6. 空关系表初始化

当 `channel_manager_area` 刚创建且为空时：

```text
不把全部 CHANNEL_MANAGER -> CHANNEL 关系当作漂移；
不触发额外二轮重采；
但标记 relation_bootstrap = true；
最终事务会写入关系表。
```

下一轮开始正常进行关系差异监控。

## 7. 结构变化摘要

查询最近批次：

```sql
SELECT
  batch_no,
  status,
  phase,
  structure_change_summary
FROM collection_run
ORDER BY id DESC
LIMIT 10;
```

摘要内容：

```text
changed
retry_strategy
relation_bootstrap
affected_grid_codes
counts
samples
```

其中 `counts` 包括：

```text
added_targets
removed_targets
changed_targets
added_relations
removed_relations
removed_areas
```

## 8. 当前配置项

```json
{
  "collection_max_workers": 24,
  "collection_hard_max_workers": 32,
  "collection_timeout_seconds": 30,
  "collection_request_retries": 2,
  "collection_retry_delay_seconds": 0.5,
  "collection_retry_strategy": "affected_grid",
  "relation_missing_disable_threshold": 2
}
```

## 9. 目标值与完成进度

目标值保存在 `metric_target` 表：

```text
period_type + area_id + indicator_id -> target_value
```

规则：

```text
REALTIME 目标用于当日实时板块；
DAY_ACC 目标用于日累计板块；
MONTH 目标用于月累计板块。
```

前端展示：

```text
有目标值：完成进度 = 完成量 / 目标值；
没有目标值：完成进度显示 --，目标列也显示 --。
```

## 10. 当前验证状态

最新验证：

```text
python -m pytest -q
218 passed
```

当前仍有 Python 3.12 SQLite datetime adapter 弃用 warning，不影响 MySQL 运行。

## 11. 性能排查与本轮优化

2026-06-17 观察到一次实时批次耗时较高：

```text
total=272.345s
orchestrate=15.485s
write=174.906s
attempts=2
session prepare≈78.8s
```

判断：

```text
两轮重采本身可以保留；
本次慢点不在 orchestrate，而主要在 session 刷新和最终写入事务；
后续优化重点看 write 子阶段，而不是盲目减少重采轮数。
```

本轮代码增加写入细分日志：

```text
驾驶舱写入阶段耗时 batch_no=... timings={
  "structure_sync_seconds": ...,
  "area_id_attach_seconds": ...,
  "run_record_seconds": ...,
  "normalize_seconds": ...,
  "metric_write_seconds": ...,
  "commit_seconds": ...
}
```

排查规则：

```text
structure_sync_seconds 高：
  看 area / request_target / channel_manager_area 结构变化是否过多。

metric_write_seconds 高：
  看 metric_snapshot 插入和 metric_current upsert 是否被锁或索引拖慢。

commit_seconds 高：
  多半是 MySQL 提交等待、外键/索引维护或并发锁等待。

area_id_attach_seconds / normalize_seconds 高：
  通常说明本轮 rows 数量异常扩大。
```

本轮同步的性能修复：

```text
CHANNEL_MANAGER -> CHANNEL 关系同步时，
原来每个 manager 可能单独查询一次父网格 target；
现在改为一次性预加载 parent target -> grid_area_id，
减少结构变化批次中的循环 SQL。
```

模型结构同步：

```text
把已存在迁移中的保留清理索引补回 ORM 模型：
ix_metric_snapshot_collected_at
ix_metric_acc_stat_date
ix_collection_run_status_created_at
```
