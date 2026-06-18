# Dashboard 结构同步改造计划与执行结果

> 历史归档说明：本文记录的是早期结构同步改造方案，当前主流程已经演进为
> `StructureGraph + channel_manager_area + 候选结构重采 + 最终事务同步`。
> 文中提到的 `services/dashboard_sync_service.py` 和
> `tests/test_dashboard_sync_service.py` 已删除；`CHANNEL` 也不再作为请求目标。
> 当前实现请以 `docs/dashboard收缩流程修复记录.md`、
> `docs/dashboard完整收缩流程图.md` 和
> `docs/dashboard数据库流程与表结构.md` 为准。

## 1. 背景

当前 dashboard 采集链路里：

- `area` 可以从采集结果自动更新
- `request_target` 主要还是从 `area` 反推 `CITY / BRANCH / GRID`
- `CHANNEL_MANAGER` 依赖 Excel / 导入链路维护

这会导致经理层结构变化（新增、迁移、失效）不能随着请求结果自动自愈，只能依赖人工维护，或者靠第二轮重采部分缓解。

本次目标是：

- 让同一轮请求结果同时驱动 `area` 和 `request_target`
- 不新增 observation 存储层
- 不新增数据库迁移
- 让 `CHANNEL_MANAGER` 不再依赖 Excel 作为日常真相源

---

## 2. 改造计划

### 2.1 总体思路

将原来的链路：

`采集结果 -> area -> request_target(只同步 CITY/BRANCH/GRID)`

改成：

`请求响应 -> rows + structure_observations -> 同步 area + 同步 request_target -> 校验 -> 必要时重采`

也就是说：

- `area` 继续从正式区域采集结果中同步
- `request_target` 不再只从 `area` 反推，而是直接从本轮请求结果和结构观察中同步
- observations 只作为内存中的中间产物，不单独落库

### 2.2 不新增 observation 存储

保留现有采集输出：

- `rows`
- `structure_observations`

不新增 observation 表，不把 observation 作为新的持久化真相层。

长期真相仍由以下表承担：

- `area`
- `request_target`
- `metric_current`
- `metric_snapshot`
- `metric_acc`

### 2.3 request_target 改为 observation 驱动同步

新增一个 observation 驱动同步函数：

- `sync_request_targets_from_observations(engine, rows, structure_observations, collected_at=None)`

同步规则：

- 从 `rows` 中提取 `CITY / BRANCH / GRID`
- 从 `structure_observations` 中提取 `GRID -> CHANNEL_MANAGER`
- 不从 `CHANNEL` 生成 `request_target`
- `CHANNEL_MANAGER.area_id = None`
- `CHANNEL_MANAGER.parent_target_id` 指向对应 `GRID` target
- 按 `(target_type, target_code)` upsert
- 对当前轮未观察到的既有启用 target 做禁用

### 2.4 area 继续由采集结果同步

保留 `sync_areas_from_acc()` 作为 `area` 主写入逻辑。

增强点：

- 优先利用 `parent_request_code`
- 对 `CHANNEL` 的父级解析优先尝试通过 `CHANNEL_MANAGER -> GRID` 的 target 关系回推
- 保留原有编码推断作为兜底

### 2.5 调整主流程

采集主流程改为：

1. 执行采集
2. 同步 `area`
3. 同步 `request_target`
4. 做 area 校验
5. 如果失败，重新加载刚同步出来的 request targets
6. 执行第二轮采集

这样首轮从 `GRID` 响应里发现的 `CHANNEL_MANAGER`，可以直接进入第二轮采集目标。

### 2.6 Excel 的角色调整

保留 Excel 导入能力，但降级为：

- 冷启动初始化工具
- 人工修复 / 兜底工具

不再依赖它维护日常经理层变化。

---

## 3. 实际修改内容

### 3.1 修改文件

本次实际修改了以下文件：

- `services/dashboard_collection_orchestrator.py`
- `services/dashboard_pipeline.py`
- `services/dashboard_sync_service.py`
- `tests/test_dashboard_pipeline.py`
- `tests/test_dashboard_sync_service.py`

### 3.2 `services/dashboard_sync_service.py`

已实现新的 observation 驱动同步逻辑：

- `sync_request_targets_from_observations()`
  - 从 `rows` 生成 `CITY / BRANCH / GRID`
  - 从 `structure_observations` 生成 `CHANNEL_MANAGER`
  - 自动建立 `CHANNEL_MANAGER -> GRID` 父子关系
  - 对缺失的既有 target 做禁用

同时保留：

- `sync_request_targets_from_areas()` 作为兼容 / 兜底逻辑

另外增强了 `sync_areas_from_acc()`：

- 优先使用 `parent_request_code`
- `CHANNEL` 可优先通过 `CHANNEL_MANAGER` 的 target 关系定位父 `GRID`

### 3.3 `services/dashboard_collection_orchestrator.py`

已将主同步顺序改成：

1. 采集
2. `sync_areas_from_acc()`
3. `sync_request_targets_from_observations()`
4. area 校验
5. 失败时重新加载 target 再采

同时：

- `structure_sync` 不再是空数组
- 现在会返回每一轮同步 `area` 和 `request_target` 的结果摘要

### 3.4 `services/dashboard_pipeline.py`

已移除原来末尾那次基于 `area` 的旧式 request_target 同步。

现在 pipeline 直接复用 orchestrator 返回的同步结果：

- `sync`
- `structure_sync`

这样主同步入口统一收敛到了 orchestrator。

---

## 4. 测试补充

### 4.1 新增测试

新增：

- `tests/test_dashboard_sync_service.py`

覆盖了：

- observation 驱动创建 `CITY / BRANCH / GRID / CHANNEL_MANAGER`
- 不为 `CHANNEL` 创建 request_target
- `CHANNEL_MANAGER.area_id is None`
- `CHANNEL_MANAGER.parent_target_id` 正确指向 `GRID`
- 缺失的 manager target 会被禁用
- 名称更新行为正确

### 4.2 扩展测试

扩展：

- `tests/test_dashboard_pipeline.py`

新增覆盖：

- 首轮从 `GRID` 响应中发现新的 `CHANNEL_MANAGER`
- 首轮同步后将 manager 写入 `request_target`
- 第二轮重采时重新加载 target，manager 会进入采集目标

---

## 5. 执行结果总结

### 5.1 达成效果

本次改造后，dashboard 的结构同步行为变成：

- `area` 仍由正式区域采集结果自动更新
- `request_target` 由本轮请求结果和结构观察自动更新
- `CHANNEL_MANAGER` 不再依赖 Excel 作为日常维护来源
- 第一轮发现的新 manager，第二轮就能参与采集

### 5.2 未做的内容

本次没有做以下内容：

- 不新增 observation 数据库存储
- 不新增数据库 migration
- 不新增 request_target 审计字段
- 不做局部补采替代整批重采
- 不大改 channel 层级模型，只做最小增强

### 5.3 回归测试结果

已执行：

```bash
python -m pytest -q tests/test_dashboard_collection_service.py tests/test_dashboard_sync_service.py tests/test_dashboard_pipeline.py
```

结果：

```text
18 passed in 1.30s
```

说明本次最小闭环改造已通过目标测试。

---

## 6. 当前结论

本次改造已经把 dashboard 的日常结构同步从“半自动”推进到“请求结果驱动自动同步”：

- 上层区域：自动同步
- 经理层节点：自动同步
- Excel：退化为初始化 / 兜底工具

后续如果还要继续优化，下一步最值得做的是：

1. 引入更稳健的 `missing_count / last_seen_at` 衰减策略到 `request_target`
2. 把整批二次重采优化成“只补采受影响节点”
3. 必要时增加 observation 调试快照，便于排查结构漂移
