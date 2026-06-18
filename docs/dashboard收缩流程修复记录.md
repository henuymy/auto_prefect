# 数据驾驶舱收缩流程梳理与修复记录

## 背景

本次修复围绕 `docs/dashboard完整收缩流程图.md` 中的目标边界：

```text
失败时只写运行状态和失败报告；
成功时才在最终短事务里写正式结构和正式指标。
```

重点修复结构漂移、重采验证、最终事务提交和指标写入前的 `area_id` 解析问题。

## 前置判断

### 1. 当前代码是否已经按推荐流程收缩

结论：

```text
已经部分收缩，但没有完全达到推荐流程。
```

已符合的部分：

```text
采集阶段只返回 rows / structure_observations / recoverable_errors
采集阶段不直接写 area
采集阶段不直接写 request_target
采集阶段不直接写 metric_*
结构漂移时已经有候选结构与重采骨架
```

未完全符合的部分：

```text
最终提交边界还不清晰
结构同步和指标写入不在同一个事务内
重采后不完整仍可能继续写指标
area_id 回填失败可能静默丢行
结构同步可能误 disable 未观察到的旧结构
配置键名存在不一致
```

### 2. 完整目标流程图

已新增文档：

- `docs/dashboard完整收缩流程图.md`

目标流程核心分支：

```text
结构没变：
  不消费 observation
  不更新 area
  不更新 request_target
  只写 metric_snapshot
  只写 metric_current / metric_acc
  collection_run = SUCCESS

结构变了：
  校验失败且确认结构漂移
  基于 observation 生成候选结构
  重采一轮
  第二次验证必须通过
  最终事务写 area / request_target / metric_* / collection_run = SUCCESS

失败：
  只写 collection_run 状态
  写失败报告
  不写正式结构
  不写正式指标
```

## 第一轮修复内容

### 1. 最终提交改为单一事务

修改文件：

- `services/dashboard_pipeline.py`
- `services/dashboard_metric_store.py`
- `services/dashboard_collection_orchestrator.py`

调整后：

```text
结构没变：
  写 metric_snapshot
  写 metric_current / metric_acc
  collection_run = SUCCESS

结构变了：
  同一事务内写 area
  同一事务内写 request_target
  基于最新 area 回填 area_id
  写 metric_snapshot
  写 metric_current / metric_acc
  collection_run = SUCCESS
```

避免了旧逻辑中“指标已写成功，结构同步随后失败”的不一致状态。

具体调整：

```text
原逻辑：
  write_metric_batch(...)
    -> 写 metric_*
    -> collection_run = SUCCESS
  然后再尝试同步 area/request_target

新逻辑：
  with session_factory.begin() as session:
    if structure_changed:
      sync_structure_in_session(...)
      attach_area_ids_in_session(...)
    write_metric_batch_in_session(...) 或 write_acc_metric_batch_in_session(...)
```

新增事务内写指标函数：

```python
write_metric_batch_in_session(...)
write_acc_metric_batch_in_session(...)
```

保留原函数：

```python
write_metric_batch(...)
write_acc_metric_batch(...)
```

这样既不破坏已有调用，又允许 pipeline 控制同一个最终事务。

### 2. 新增事务内结构同步 helper

修改文件：

- `services/dashboard_collection_orchestrator.py`

新增：

```python
sync_structure_in_session(...)
```

用途：

```text
让调用方传入已有 SQLAlchemy session；
结构同步不再自己新开事务；
与指标写入、collection_run=SUCCESS 处在同一个最终事务内。
```

### 3. 新增基于最新 area 回填 area_id 的 helper

修改文件：

- `services/dashboard_collection_orchestrator.py`

新增：

```python
attach_area_ids_in_session(...)
```

用途：

```text
结构漂移时，新区域刚在同一个事务里写入 area；
采集 rows 只有 level_type / area_code / area_name；
写 metric_* 必须使用正式 area_id；
所以需要基于事务内最新 area 表，把 area_id 回填到 rows。
```

例子：

```text
采集行：
  CHANNEL / C001 / 渠道1 / metric=3 / area_id=?

同一事务先写 area：
  id=4 / CHANNEL / C001 / 渠道1

回填后：
  CHANNEL / C001 / 渠道1 / metric=3 / area_id=4

最终写 metric_snapshot：
  area_id=4 / metric_value=3
```

### 4. 补充缺失导入

修改文件：

- `services/dashboard_collection_orchestrator.py`

补充：

```python
from typing import Iterable
```

原因：

```text
新增 attach_area_ids_in_session(...) 的 rows 参数使用了 Iterable 类型标注。
```

## 第二轮修复内容

### 1. 重采后仍不完整时必须失败

修改文件：

- `services/dashboard_collection_orchestrator.py`
- `tests/test_dashboard_pipeline.py`

问题：

第二轮校验如果只返回部分 `matched_rows`，旧逻辑可能把这些部分行当成有效结果继续写指标。

修复后：

```text
第二轮校验未完整通过
  -> 抛 AreaCoverageError
  -> 不写 area/request_target
  -> 不写 metric_*
  -> 批次失败
```

### 2. area_id 回填失败时不再静默丢行

修改文件：

- `services/dashboard_collection_orchestrator.py`
- `tests/test_dashboard_pipeline.py`

新增：

```python
AreaIdResolutionError
attach_area_ids_in_session(...)
```

结构变化后，指标写入前会基于事务内最新 `area` 表回填正式 `area_id`。

如果仍有采集行找不到 `area_id`：

```text
抛 AreaIdResolutionError
最终事务回滚
不允许少写指标
```

### 3. 结构同步不再自动禁用未观察到的旧结构

修改文件：

- `services/dashboard_collection_orchestrator.py`

问题：

旧逻辑会将本轮采集没有观察到的旧 `area` / `request_target` 标记为 disabled。若平台临时漏数或本轮不是严格全量覆盖，可能误禁用正常结构。

修复后：

```text
结构同步只新增/更新本轮确认观察到的结构；
不因本轮未出现就自动 disable 旧 area/request_target。
```

### 4. 修复配置键名不一致

修改文件：

- `services/dashboard_pipeline.py`

配置文件中已有字段：

```json
"collection_max_channel_fallback_requests": 50
```

pipeline 之前读取的是 `max_fallback_requests`，导致现有配置不生效。

修复后优先读取：

```text
collection_max_channel_fallback_requests
```

并兼容旧字段：

```text
max_fallback_requests
```

### 5. 修复失败报告分支缺失导入

修改文件：

- `services/dashboard_batch_runner.py`

补充：

```python
import json
```

避免失败报告分支生成 `error_message` 时因为缺少导入而吞掉状态更新。

## 新增/调整测试

修改文件：

- `tests/test_dashboard_pipeline.py`

覆盖场景：

```text
结构漂移后构建候选结构重采，不立即写库
第二轮部分通过必须失败
结构同步后 area_id 回填失败必须报错
```

## 验证结果

已执行：

```powershell
$env:PYTHONPATH=(Get-Location).Path; pytest tests/test_dashboard_pipeline.py tests/test_dashboard_metric_store.py tests/test_dashboard_simple_collection.py
```

结果：

```text
26 passed
```

已执行编译检查：

```powershell
$env:PYTHONPATH=(Get-Location).Path; python -m py_compile services\dashboard_pipeline.py services\dashboard_metric_store.py services\dashboard_collection_orchestrator.py services\dashboard_batch_runner.py
```

结果：

```text
通过
```

## 重新 Review 结论

复审重点：

```text
结构漂移分支
最终事务边界
失败状态
area_id 回填
父子关系推断
候选结构构建
```

复审结果：

```text
测试通过，编译通过；
本轮已修复的“部分写入”“静默丢行”“误 disable”问题已被压住；
但结构漂移识别和父子关系推断仍存在后续风险。
```

### 1. GRID 父级 request_target 推断可能错误

位置：

- `services/dashboard_collection_orchestrator.py`

当前逻辑中：

```text
area 的 GRID 父级推断使用 area_code[:2]
request_target 的 GRID 父级推断使用 target_code[:1]
```

风险：

```text
若真实编码为 AQ701 -> AQ，
target_code[:1] 会得到 A，
可能导致 GRID 的 request_target 父级挂错或为空。
```

影响：

```text
后续 CHANNEL_MANAGER -> GRID -> area_id 的链路可能受影响。
```

### 2. 新 CHANNEL_MANAGER 可能无法触发结构漂移

位置：

- `services/dashboard_collection_orchestrator.py`
- `services/dashboard_area_validation.py`
- `services/dashboard_collection_service.py`

当前结构漂移判断依赖：

```text
区域校验抛异常
且 unmatched_areas 与 structure_observations 有交集
```

风险：

```text
CHANNEL_MANAGER 是路径节点，不是正式 area；
验证层会忽略 CHANNEL_MANAGER；
因此“网格下新增/变更渠道经理”可能只出现在 structure_observations，
但不会出现在 unmatched_areas，
从而无法触发重采。
```

影响：

```text
新增经理 -> 需要重采该经理下渠道
这个场景可能仍然走不到重采分支。
```

### 3. 候选 area_map 只来自首轮 rows，可能缺少第二轮新渠道

位置：

- `services/dashboard_collection_orchestrator.py`

当前逻辑：

```text
结构漂移后，candidate_area_map 从首轮 collection_result["rows"] 构建。
```

风险：

```text
若首轮只发现新 CHANNEL_MANAGER，
第二轮才通过该经理采到新 CHANNEL，
候选 area_map 中不会包含这些第二轮新 CHANNEL。
```

影响：

```text
第二轮校验可能仍失败；
正常的“新增经理 -> 新增渠道”结构变化无法一轮修复完成。
```

### 4. 候选 target 临时 ID 使用 hash，存在不稳定性

位置：

- `services/dashboard_collection_orchestrator.py`

当前逻辑：

```python
id=-hash(identity) % (10 ** 8)
```

风险：

```text
Python hash 有随机化；
理论上可能碰撞；
临时 ID 会参与 parent_target_id 和采集结果归集。
```

建议：

```text
改为本轮内递增负数 ID：
-1, -2, -3 ...
确定、无碰撞、可读。
```

## 第三轮修复内容：渠道减少自动收缩

### 1. 当前 schema 约束

复核模型后确认：

```text
request_target 支持：
  CITY
  BRANCH
  GRID
  CHANNEL_MANAGER

request_target 当前没有 CHANNEL 类型。
```

因此“CHANNEL_MANAGER 下面渠道数变少”在当前数据库模型里不能直接禁用某条 channel 级 `request_target`，因为这条记录不存在。

当前能自动更新的是：

```text
area 表中的 CHANNEL 行
```

也就是说，本轮实现的自动收缩策略是：

```text
可信采集下发现 enabled CHANNEL 本轮消失
  -> 作为 SHRINK_DRIFT
  -> 候选 area_map 中排除该 CHANNEL
  -> 重采一轮
  -> 第二轮校验通过后，在最终事务中禁用 area.CHANNEL
```

### 2. 收缩检测条件

修改文件：

- `services/dashboard_collection_orchestrator.py`

新增：

```python
detect_shrink_candidates(...)
without_removed_areas(...)
```

收缩候选只在以下条件成立时触发：

```text
本轮没有 recoverable_errors
当前校验未完整通过
缺失的 enabled area 全部是 CHANNEL
非 CHANNEL 区域没有缺失
```

这样可以避免：

```text
CITY / BRANCH / GRID 缺失时误判成渠道收缩
经理请求失败时误判成渠道收缩
平台返回不完整时直接改结构
```

### 3. 收缩重采流程

收缩触发后：

```text
首轮 area_map:
  A
  AQ701
  C001
  C002

首轮 rows:
  A
  AQ701
  C001

缺失:
  C002

候选 area_map:
  A
  AQ701
  C001

使用原 request_target 重采
第二轮按候选 area_map 校验
```

第二轮必须完整通过，才允许进入最终事务。

### 4. 最终事务中的收缩提交

修改文件：

- `services/dashboard_pipeline.py`
- `services/dashboard_collection_orchestrator.py`

新增返回：

```python
change_plan = {
    "expanded": False,
    "removed_areas": [...]
}
```

最终事务调用：

```python
sync_structure_in_session(..., change_plan=change_plan)
```

当 `removed_areas` 中包含 `CHANNEL` 时：

```text
area.enabled = false
area.missing_count += 1
```

注意：

```text
当前不会禁用 request_target，因为 request_target 没有 CHANNEL 类型。
```

### 5. 新增测试

修改文件：

- `tests/test_dashboard_pipeline.py`

新增覆盖：

```text
渠道减少时生成 removed_areas change_plan，并进行候选重采
最终事务能禁用 removed CHANNEL area
```

验证结果更新：

```text
28 passed
```

## 第四轮修复内容：扩张与候选结构打磨

重新 review 后继续修复了几个容易影响真实结构变化的点。

### 1. 新 CHANNEL_MANAGER 可触发重采

问题：

```text
新增 CHANNEL_MANAGER 通常只出现在 GRID 响应的 structure_observations 中；
第一次采集不会请求这个新经理；
因此它下面的新 CHANNEL 也不会进入 rows；
原逻辑可能在区域校验通过后直接结束，漏掉新增经理。
```

新增：

```python
detect_manager_target_drift(...)
```

逻辑：

```text
比较当前 request_target 中 GRID -> CHANNEL_MANAGER 集合
和本轮 GRID 响应观察到的 CHANNEL_MANAGER 集合。

如果不一致：
  视为结构漂移
  构建候选 request_target
  重采一轮
```

### 2. 第二轮出现新 CHANNEL 时允许候选结构自校验

问题：

```text
第一轮发现新经理；
第二轮请求新经理后才发现新渠道；
此时新渠道尚未在数据库 area 表中；
如果仍只按旧 area_map 校验，会失败。
```

新增：

```python
validate_rows_with_candidate_area_map(...)
```

逻辑：

```text
在最后一轮检测到结构漂移时，
用本轮 rows 构建候选 area_map，
再对候选结构自身做完整性校验。

通过后：
  允许进入最终事务；
  最终事务写 area / request_target / metric_* / SUCCESS。
```

### 3. 候选 target 临时 ID 改为稳定递减负数

问题：

旧逻辑：

```python
id=-hash(identity) % (10 ** 8)
```

风险：

```text
Python hash 有随机化；
理论上可能碰撞；
临时 ID 参与 parent_target_id。
```

修复后：

```text
使用本轮内递减负数：
-1, -2, -3 ...
```

这样候选结构更稳定、可读，也不会碰撞。

### 4. 修复 GRID 父级 request_target 推断

问题：

```text
area 的 GRID 父级推断使用 area_code[:2]
request_target 的 GRID 父级推断使用 target_code[:1]
```

修复后：

```text
request_target 的 GRID 父级也使用 target_code[:2]
```

与样例编码：

```text
AQ701 -> AQ
```

保持一致。

### 5. 新增扩张真实路径测试

新增测试：

```text
新经理第一次只出现在 GRID structure_observations；
第二次请求新经理后采到新 CHANNEL；
候选结构自校验通过；
返回 expanded change_plan。
```

验证结果更新：

```text
29 passed
```

## 当前剩余注意点

父子关系推断规则已经在主收缩链路中统一：

```text
area GRID 父级推断使用 area_code[:2]
request_target GRID 父级推断使用 target_code[:2]
```

后续仍建议结合真实编码规则做一次生产数据抽样确认，但当前代码里这两个规则已经不再互相冲突。

## 第五轮 Review：按“只请求 manager 获取渠道”方案复审

### 1. 推荐方案边界

为了避免请求 `CHANNEL` 导致请求量爆炸，推荐边界应固定为：

```text
request_target 只保存可请求节点：
  CITY
  BRANCH
  GRID
  CHANNEL_MANAGER

area 只保存正式区域：
  CITY
  BRANCH
  GRID
  CHANNEL

采集时：
  请求 GRID 可发现 CHANNEL_MANAGER
  请求 CHANNEL_MANAGER 可返回 CHANNEL 指标行
  不把 CHANNEL 作为常规请求目标
```

也就是说：

```text
CHANNEL_MANAGER 是请求叶子；
CHANNEL 是采集结果和展示区域；
CHANNEL 不应该进入 request_target 队列。
```

### 2. 当前主流程符合的部分

复审代码后，主收缩链路基本符合这个方案。

关键依据：

```text
services/dashboard_request_target_import.py
  TARGET_TYPES = {"CITY", "BRANCH", "GRID", "CHANNEL_MANAGER"}

services/dashboard_collection_orchestrator.py
  _sync_targets_in_session(...)
    只从 rows 生成 CITY / BRANCH / GRID
    只从 GRID -> CHANNEL_MANAGER observation 生成 CHANNEL_MANAGER
    不从 CHANNEL 行生成 request_target

services/dashboard_collection_service.py
  CHANNEL_MANAGER 响应会被解析成 CHANNEL 指标行
```

因此正常路径是：

```text
request_target.GRID
  -> 请求平台
  -> observation 发现 CHANNEL_MANAGER
  -> 候选 request_target 增加 CHANNEL_MANAGER
  -> 重采

request_target.CHANNEL_MANAGER
  -> 请求平台
  -> rows 产生 CHANNEL 指标
  -> area 写 CHANNEL
  -> metric_* 写 CHANNEL 指标
```

这个路径不会因为渠道数量很多而直接发起几千个 `CHANNEL` 请求。

### 3. 需要继续收紧的风险点

#### 风险 1：脏数据中的 CHANNEL request_target 仍可能被执行

位置：

```text
services/dashboard_collection_service.py
```

当前 `load_collection_targets(...)` 读取所有 enabled `request_target`：

```text
select(RequestTarget).where(RequestTarget.enabled.is_(True))
```

而 `extract_target_metric_rows(...)` 中：

```text
FORMAL_TARGET_TYPES = {"CITY", "BRANCH", "GRID", "CHANNEL"}
```

这意味着：

```text
虽然导入和新同步逻辑不会创建 CHANNEL request_target，
但如果数据库里历史残留或手工写入了 enabled CHANNEL target，
采集器仍会把它当成合法目标并请求。
```

这和“不要请求 CHANNEL”的目标边界不完全一致。

建议修复：

```text
load_collection_targets(...) 增加白名单过滤：
  RequestTarget.target_type.in_(["CITY", "BRANCH", "GRID", "CHANNEL_MANAGER"])

并且最好额外拒绝 CHANNEL：
  如果读到 CHANNEL request_target，记录告警或直接抛错。
```

更严格的做法：

```text
把 services/dashboard_collection_service.py 的 FORMAL_TARGET_TYPES
从 {"CITY", "BRANCH", "GRID", "CHANNEL"}
改成 {"CITY", "BRANCH", "GRID"}。

CHANNEL 只允许作为 CHANNEL_MANAGER 响应解析出来的 row，
不允许作为请求目标被 extract_target_metric_rows(...) 接受。
```

#### 风险 2：recover_missing_channels 仍保留渠道直查能力

位置：

```text
services/dashboard_collection_orchestrator.py
```

当前仍存在：

```python
recover_missing_channels(...)
```

该 helper 会构造：

```text
CollectionTarget(target_type="CHANNEL")
```

用于缺失渠道直查。

复审结果：

```text
当前 collect_validate_metric_rows_simple(...) 主链路没有调用它；
它主要被测试覆盖，属于残留能力。
```

但从方案边界看，它仍然是一个潜在退化口：

```text
以后如果有人重新接入该 helper，
系统可能再次从 manager 聚合采集退化成 CHANNEL 直查。
```

建议处理：

```text
默认彻底关闭：
  collection_max_channel_fallback_requests = 0

或者直接废弃 recover_missing_channels(...)：
  缺渠道时不做 CHANNEL 直查；
  只触发 GRID / CHANNEL_MANAGER 结构重采；
  仍不完整则失败，不写库。
```

如果保留为诊断工具，建议改名表达边界：

```text
diagnose_missing_channels_by_direct_request(...)
```

并且只允许人工诊断入口调用，不进入正式批次链路。

#### 风险 3：配置默认值仍允许 50 次渠道直查

位置：

```text
config/dashboard/session.json
services/dashboard_pipeline.py
```

当前配置：

```json
"collection_max_channel_fallback_requests": 50
```

当前 pipeline 会读取这个值并传入编排函数。

虽然主链路暂时没有使用 `recover_missing_channels(...)`，但从目标设计看，这个配置会误导后续维护者，以为“少量 channel 直查”仍是正式策略。

建议：

```json
"collection_max_channel_fallback_requests": 0
```

并在文档中明确：

```text
正式采集不做 CHANNEL 直查；
缺失渠道只能通过 manager / grid 重采解决；
重采仍不完整则失败。
```

#### 风险 4：旧同步服务仍保留旧式 disable 行为

位置：

```text
services/dashboard_sync_service.py
```

当前主 pipeline 已经改用：

```text
services/dashboard_collection_orchestrator.py
  sync_structure_in_session(...)
```

但旧服务中仍存在：

```text
sync_areas_from_acc(...)
  未观察到的旧 area 会被 disabled

sync_request_targets_from_observations(...)
  未观察到的旧 request_target 会被 disabled

sync_request_targets_from_areas(...)
  未观察到的旧 request_target 会被 disabled
```

风险：

```text
如果后续有人从旧入口调用这些函数，
可能绕过“候选结构 + 重采确认 + 最终事务”的新收缩保护，
重新出现误禁用 area/request_target 的问题。
```

建议：

```text
给旧函数加 deprecated 注释；
或改成与新逻辑一致：
  默认不 disable 未观察到结构；
  只有显式传入 confirmed_removed_areas / confirmed_removed_targets 才禁用。
```

#### 风险 5：manager 下渠道变少只能精确到 CHANNEL area，不能表达 manager-channel 关系

当前数据库模型没有单独保存：

```text
CHANNEL_MANAGER -> CHANNEL
```

`area.CHANNEL.parent_id` 最终只指向 `GRID`，`request_target` 只保存 `GRID -> CHANNEL_MANAGER`。

所以当前能表达的是：

```text
某个 CHANNEL 是否仍是有效正式区域
```

不能完整表达：

```text
某个 CHANNEL 从 manager A 转移到 manager B
某个 CHANNEL 只从 manager A 消失但仍归属于同一 GRID
```

如果业务只关心“渠道是否存在于网格下”，当前模型够用。

如果业务要精确管理“经理-渠道归属变化”，建议新增关系表：

```text
channel_manager_area
  manager_target_id
  channel_area_id
  grid_area_id
  enabled
  last_seen_at
```

这样收缩可以从：

```text
禁用 CHANNEL area
```

升级为：

```text
禁用 manager-channel 关系；
只有所有 manager 都不再返回该 channel 时，才禁用 CHANNEL area。
```

### 4. 本轮 Review 结论

当前主采集流程已经朝正确方向收缩：

```text
不新增 CHANNEL request_target；
新增 manager 通过 GRID observation 自动发现；
CHANNEL 通过 manager 请求回填为 area 和 metric；
渠道减少通过候选 area_map + 重采确认后禁用 area.CHANNEL。
```

但要让“只请求 manager，不请求 channel”变成强约束，还建议继续做三件事：

```text
1. load_collection_targets(...) 显式过滤 request_target 类型，拒绝 CHANNEL；
2. 删除或隔离 recover_missing_channels(...)，正式批次不允许渠道直查；
3. 将 collection_max_channel_fallback_requests 默认改为 0，并把旧同步服务标记为 deprecated 或同步收紧。
```

### 5. 本轮验证

已执行：

```powershell
$env:PYTHONPATH=(Get-Location).Path; pytest tests/test_dashboard_pipeline.py tests/test_dashboard_metric_store.py tests/test_dashboard_simple_collection.py
```

结果：

```text
29 passed
```

## 第六轮清理：删除多余 dashboard 代码

按“只请求 `CHANNEL_MANAGER` 获取渠道，不请求 `CHANNEL`”的边界，继续删除了几块容易误导后续维护的旧代码。

### 1. 删除渠道直查 fallback

删除内容：

```text
services/dashboard_collection_orchestrator.py
  recover_missing_channels(...)
```

同时移除：

```text
collect_validate_metric_rows_simple(..., max_fallback_requests=...)
DashboardBatchContext.collect_validate_simple(..., max_fallback_requests=...)
execute_dashboard_pipeline(...) 中 collection_max_channel_fallback_requests 读取
tests/test_dashboard_pipeline.py 中直接补采 CHANNEL 的测试
config/dashboard/session.json 中 collection_max_channel_fallback_requests 配置
```

清理后正式批次不再保留“缺哪个渠道就直接请求哪个 CHANNEL”的退化路径。

### 2. 采集目标白名单排除 CHANNEL

修改内容：

```text
services/dashboard_collection_service.py
```

现在读取请求目标时只允许：

```text
CITY
BRANCH
GRID
CHANNEL_MANAGER
```

并且正式请求目标集合只保留：

```text
CITY
BRANCH
GRID
```

`CHANNEL` 只能由 `CHANNEL_MANAGER` 响应解析为指标行，不能再作为合法请求目标被采集器执行。

### 3. 删除旧结构同步服务

删除内容：

```text
services/dashboard_sync_service.py
tests/test_dashboard_sync_service.py
```

原因：

```text
旧服务已不在主 pipeline 中使用；
旧服务仍保留“未观察到就 disable”的历史行为；
与当前“候选结构 + 重采确认 + 最终事务提交”的收缩策略冲突。
```

当前结构同步入口统一收敛到：

```text
services/dashboard_collection_orchestrator.py
  sync_structure_in_session(...)
```

### 4. 清理后目标边界

最终边界变为：

```text
request_target:
  CITY / BRANCH / GRID / CHANNEL_MANAGER

area:
  CITY / BRANCH / GRID / CHANNEL

采集：
  GRID -> 发现 CHANNEL_MANAGER
  CHANNEL_MANAGER -> 返回 CHANNEL 指标行
  不请求 CHANNEL
```

## 第七轮改造：候选结构图与 manager-channel 关系同步

本轮目标是统一处理：

```text
CHANNEL_MANAGER 增加
CHANNEL_MANAGER 减少
CHANNEL 增加
CHANNEL 减少
CHANNEL 从经理 A 转到经理 B
```

核心改法：

```text
第一轮只观察，不写正式结构；
根据 rows + structure_observations 生成候选结构图；
第二轮按候选 request_target 重采；
第二轮必须完整通过；
最终短事务一次性提交 area / request_target / channel_manager_area / metric_*。
```

### 1. 新增关系表

新增模型：

```text
models/dashboard_channel_manager_area.py
```

新增迁移：

```text
migrations/dashboard/versions/20260616_0013_create_channel_manager_area.py
```

表：

```text
channel_manager_area
```

字段：

```text
manager_target_id
channel_area_id
grid_area_id
enabled
last_seen_at
missing_count
```

唯一约束：

```text
(manager_target_id, channel_area_id)
```

这张表专门表达：

```text
CHANNEL_MANAGER -> CHANNEL
```

这样才能区分：

```text
渠道真的消失了
渠道只是从经理 A 转到了经理 B
经理消失了，但渠道可能还在另一个经理下
```

### 2. CHANNEL_MANAGER 减少进入 removed_targets

修改文件：

```text
services/dashboard_collection_orchestrator.py
```

新增：

```python
detect_manager_target_changes(...)
```

逻辑：

```text
对比当前 request_target 中 GRID -> CHANNEL_MANAGER 集合
和本轮 GRID 响应观察到的 CHANNEL_MANAGER 集合。
```

如果发现旧 manager 消失：

```python
change_plan["removed_targets"] = [
  {
    "target_type": "CHANNEL_MANAGER",
    "target_code": "...",
    "parent_type": "GRID",
    "parent_code": "..."
  }
]
```

最终事务中：

```text
禁用 request_target.CHANNEL_MANAGER
同时禁用该 manager 下面的 channel_manager_area 关系
```

### 3. manager-channel 关系漂移也触发重采

新增：

```python
detect_manager_channel_relation_drift(...)
```

覆盖场景：

```text
manager 集合没变；
area 覆盖也完整；
但 CHANNEL 从 M001 转到 M002。
```

这种情况下，旧逻辑可能认为校验通过，直接写指标，不更新关系。

现在会：

```text
检测 CHANNEL_MANAGER -> CHANNEL observation 与 channel_manager_area 不一致
  -> 触发结构漂移
  -> 候选结构重采
  -> 第二轮全量通过后更新关系表
```

### 4. CHANNEL 减少改为最终 rows 判断

原先的渠道收缩更多依赖校验结果里的 missing。

现在新增：

```python
detect_removed_channel_areas(...)
```

它基于最终可信采集结果判断：

```text
当前 enabled CHANNEL
  如果第二轮最终 rows 中完全没有出现
  且没有 recoverable_errors
  才放入 removed_areas
```

这样可以避免误删：

```text
CHANNEL 从 M001 转到 M002
```

因为只要最终 rows 中仍然有这个 CHANNEL，就不会禁用 `area.CHANNEL`。

### 5. 最终事务同步顺序

结构变化后，最终事务现在执行：

```text
1. _sync_areas_in_session(...)
2. _sync_targets_in_session(..., removed_targets=...)
3. _sync_manager_channel_relations_in_session(...)
4. attach_area_ids_in_session(...)
5. write_metric_batch_in_session(...) 或 write_acc_metric_batch_in_session(...)
6. collection_run = SUCCESS
```

如果任一步失败：

```text
整个事务回滚；
不写部分结构；
不写部分指标。
```

### 6. 新增测试覆盖

新增/调整测试：

```text
test_manager_shrink_builds_removed_targets_for_retry
test_sync_structure_updates_manager_channel_relations
test_sync_structure_disables_removed_channel_area
```

覆盖：

```text
GRID 下 CHANNEL_MANAGER 减少后生成 removed_targets
最终事务创建新的 manager-channel 关系
最终事务禁用旧的 manager-channel 关系
最终事务仍能禁用确认消失的 CHANNEL area
```

## 第八轮优化：显式 StructureGraph 树结构

本轮把原先分散在多个变量里的结构判断，收敛成显式的结构图对象。

### 1. 新增结构对象

修改文件：

```text
services/dashboard_collection_orchestrator.py
```

新增：

```python
StructureNode
StructureEdge
StructureGraph
StructureDiff
```

现在结构图统一表达：

```text
areas:
  CITY / BRANCH / GRID / CHANNEL

targets:
  CITY / BRANCH / GRID / CHANNEL_MANAGER

edges:
  GRID -> CHANNEL_MANAGER
  CHANNEL_MANAGER -> CHANNEL
```

### 2. current_graph / observed_graph / candidate_graph

当前数据库结构：

```python
current_graph = load_current_structure_graph(engine, targets)
```

本轮采集观察结构：

```python
observed_graph = build_observed_structure_graph(rows, structure_observations)
```

结构变化对比：

```python
graph_diff = diff_structure_graph(current_graph, observed_graph)
```

候选结构仍然不立即写库，而是由 `observed_graph` 派生：

```text
candidate_area_map
candidate_request_targets
candidate_manager_channel_relations
```

### 3. 变化监控统一由 graph diff 负责

原先这些判断分散在多个函数里：

```text
manager 是否新增/减少
manager-channel 关系是否变化
channel 是否完全消失
```

现在统一由：

```python
diff_structure_graph(...)
```

输出：

```text
added_targets
removed_targets
changed_targets
added_relations
removed_relations
removed_areas
```

只要这些集合不为空，就说明结构有变化，需要进入：

```text
结构漂移 -> 候选结构 -> 第二轮重采 -> 第二轮完整通过 -> 最终事务提交
```

### 4. 兼容未迁移环境

如果数据库还没有：

```text
channel_manager_area
```

则 graph diff 会暂时跳过 `CHANNEL_MANAGER -> CHANNEL` 关系对比。

迁移完成后，该关系对比自动生效。

### 5. 验证结果

已执行：

```powershell
$env:PYTHONPATH=(Get-Location).Path; $files = Get-ChildItem tests -Filter 'test_dashboard_*.py' | ForEach-Object { $_.FullName }; pytest $files
```

结果：

```text
82 passed
```

## 第九轮方案选择：渠道变化少时采用局部重采

针对“日常渠道变化很少”的业务特点，对两种重采策略做了取舍。

### 1. 方案 A：受影响 GRID 子树重采

流程：

```text
第一轮按当前 request_target 正常采集；
如果结构没变，直接写指标；
如果某个 GRID 下 manager/channel 变化，只重采这个 GRID 子树。
```

例子：

```text
第一轮请求：
  A
  AQ
  AQ701
  AQ702
  M001
  M002
  M010

发现只有 AQ701 变化：
  原来 AQ701 -> M001, M002
  现在 AQ701 -> M001, M003

第二轮只请求：
  AQ701
  M001
  M003
```

最终组合：

```text
未受影响区域：
  使用第一轮 rows

受影响 AQ701 子树：
  使用第二轮 rows
```

优点：

```text
日常结构不变时只采一轮；
变化时只补受影响子树；
请求量最小；
适合渠道变化少的日常定时采集。
```

代价：

```text
同一个 batch 里，未受影响区域和受影响子树可能来自相差几秒的两轮请求。
```

### 2. 方案 C：先结构探测，再正式采指标

流程：

```text
第一阶段：
  请求所有 GRID，拿最新 GRID -> CHANNEL_MANAGER 结构；

第二阶段：
  按最新候选 request_target 采集指标。
```

例子：

```text
先请求：
  AQ701
  AQ702

得到：
  AQ701 -> M001, M003
  AQ702 -> M010

再请求：
  A
  AQ
  AQ701
  AQ702
  M001
  M003
  M010
```

优点：

```text
结构变化变成正式前置流程；
不会先用旧 manager 白采一轮；
最终指标来自同一套最新结构。
```

代价：

```text
每一轮都会额外请求所有 GRID；
如果结构变化很少，这个固定成本不划算。
```

### 3. 当前推荐

在“渠道变化少”的前提下，推荐：

```text
日常定时采集：
  采用方案 A：受影响 GRID 子树重采

低峰校准任务：
  可采用方案 C：先结构探测，再正式采指标
```

理由：

```text
日常快；
变化时局部修；
低峰再做全量结构校准。
```

最终策略：

```text
默认不每轮全量结构探测；
只有 graph_diff 发现变化时，才进入第二轮；
第二轮优先收敛到受影响 GRID 子树，而不是整批 request_target 全量重采。
```

## 第十轮实现：方案 A 受影响 GRID 子树重采

本轮已将代码从“结构变化后整批候选目标重采”改成“优先局部子树重采”。

### 1. 受影响 GRID 计算

修改文件：

```text
services/dashboard_collection_orchestrator.py
```

新增：

```python
_affected_grid_codes_from_diff(...)
_manager_codes_for_grids(...)
```

根据 `StructureDiff` 识别受影响 GRID：

```text
manager 新增/减少/改名 -> 对应 parent GRID
manager-channel 关系新增/减少 -> manager 所属 GRID
CHANNEL 完全消失 -> 原 manager 所属 GRID
```

### 2. 局部 retry targets

新增：

```python
_build_subtree_retry_targets(...)
```

第二轮只请求：

```text
受影响 GRID
受影响 GRID 下候选 CHANNEL_MANAGER
```

不会重采：

```text
未受影响 CITY
未受影响 BRANCH
未受影响 GRID
未受影响 CHANNEL_MANAGER
CHANNEL
```

例子：

```text
第一轮请求：
  A / AQ701 / M001 / M002

发现 AQ701 下 M002 消失，M001 保留：

第二轮只请求：
  AQ701 / M001
```

### 3. 合并第一轮与第二轮结果

新增：

```python
_merge_subtree_retry_collection(...)
```

合并规则：

```text
第一轮未受影响 rows 保留；
第一轮受影响 GRID 子树 rows 删除；
第二轮受影响 GRID 子树 rows 加入；
structure_observations 同样按受影响子树替换。
```

这样最终入库仍是完整 rows：

```text
未受影响区域：
  第一轮结果

受影响 GRID 子树：
  第二轮结果
```

### 4. 防止旧经理渠道重复

局部 merge 时不仅剔除第二轮候选 manager，还会剔除 current_graph 中该 GRID 下的旧 manager。

解决场景：

```text
AQ701 原来有 M001 / M002；
第二轮候选只剩 M001；
第一轮里 M002 返回过 C001；
如果不剔除 M002 的 rows，会出现 C001 重复。
```

当前处理：

```text
受影响 GRID 下所有旧 manager 和候选 manager 的 CHANNEL rows 都用第二轮替换。
```

### 5. 测试覆盖

新增断言：

```text
新 manager 场景：
  CITY A 只请求 1 次
  GRID AQ701 请求 2 次
  新 CHANNEL_MANAGER M001 请求 1 次

manager 减少场景：
  CITY A 只请求 1 次
  GRID AQ701 请求 2 次
  保留 manager M001 请求 2 次
  删除 manager M002 只请求 1 次
```

验证结果：

```text
82 passed
```

## 第十轮优化：重采策略开关与候选校验收紧

这轮基于 review 继续收紧两个线上风险点：

```text
1. 受影响 GRID 子树重采虽然快，但需要能配置切换到全量候选重采；
2. 第二轮候选结构自校验不能无条件相信 rows，必须先排除脏数据。
```

### 1. 新增重采策略配置

新增配置：

```json
"collection_retry_strategy": "affected_grid"
```

支持值：

```text
affected_grid
  默认策略；
  只重采受影响 GRID 和候选 CHANNEL_MANAGER；
  适合日常渠道变化少的场景。

full
  第二轮使用本轮观测构建完整 candidate_targets；
  会重采所有候选 CITY / BRANCH / GRID / CHANNEL_MANAGER；
  适合结构大调整、低峰期校准、或怀疑局部合并不够稳的场景。
```

代码入口：

```text
config/dashboard/session.json
  -> services/dashboard_pipeline.py
  -> DashboardBatchContext.collect_validate_simple(...)
  -> collect_validate_metric_rows_simple(...)
```

默认仍是 `affected_grid`，因此不改变现有日常速度策略。

### 2. 第二轮候选校验收紧

`validate_rows_with_candidate_area_map(...)` 原来只做：

```text
rows -> candidate_area_map -> validate_metric_rows -> 覆盖数量校验
```

现在增加了前置条件：

```text
存在 recoverable_errors
  -> 拒绝候选结构入库

candidate_area_map 为空
  -> 拒绝候选结构入库

validate_metric_rows 出现 skipped_rows
  -> 拒绝候选结构入库

指标值不能解析为合法数字
  -> 拒绝候选结构入库
```

这样可以避免第二轮仍有 manager 请求异常、空结构、脏行、或指标值异常时，被“候选自校验”误放行。

### 3. `channel_manager_area` 迁移兼容保护

前面已经补了迁移：

```text
migrations/dashboard/versions/20260616_0013_create_channel_manager_area.py
```

并在真实库执行后确认：

```text
database: dashboard
revision: 20260616_0013
channel_manager_area: exists
row_count: 0
```

代码层也补了兼容保护：

```text
如果 channel_manager_area 表不存在：
  只跳过 CHANNEL_MANAGER -> CHANNEL 关系同步；
  不影响 area / request_target / metric_* 写入。
```

注意这里使用的是当前事务连接做 `inspect(session.connection())`，避免探表时干扰正在进行的事务。

### 4. 新增测试覆盖

新增覆盖：

```text
full retry strategy:
  第二轮会重采完整候选目标；
  CITY / GRID 会按完整候选重新请求；
  CHANNEL_MANAGER 仍由 GRID observation 生成。

candidate validation rejects recoverable errors:
  第二轮仍有可恢复 manager 错误时，不允许候选结构自校验通过。

missing channel_manager_area table:
  缺表时关系同步返回 skipped=1；
  request_target 仍能正常同步 CHANNEL_MANAGER。
```

验证结果：

```text
32 passed
```

## 第十一轮优化：工程清理与历史文档标记

### 1. 清理 `datetime.utcnow()` 弃用调用

Python 3.12 下 `datetime.utcnow()` 会产生弃用警告。

本轮调整：

```text
services/dashboard_retention_service.py
tests/test_dashboard_retention_service.py
```

从：

```python
datetime.utcnow()
```

改为：

```python
datetime.now(UTC).replace(tzinfo=None)
```

原因：

```text
保留原来的 UTC 语义；
继续使用 naive datetime，避免和当前数据库 DATETIME 比较逻辑产生不兼容；
消除项目代码自身的 Python 3.12 弃用警告。
```

调整后全量测试 warning 从 37 降到 27，剩余 warning 来自 SQLAlchemy/SQLite 默认 date/datetime adapter，不是项目代码中的 `utcnow()` 调用。

### 2. 标记早期结构同步文档为历史归档

`docs/dashboard结构同步改造计划与执行结果.md` 是早期方案记录，其中仍包含：

```text
services/dashboard_sync_service.py
tests/test_dashboard_sync_service.py
不新增数据库迁移
旧 observation 驱动同步描述
```

这些内容与当前实现已经不一致。

本轮在文档顶部增加历史归档说明，明确当前实现以以下文档为准：

```text
docs/dashboard收缩流程修复记录.md
docs/dashboard完整收缩流程图.md
docs/dashboard数据库流程与表结构.md
```

### 3. 验证结果

```text
python -m pytest -q
186 passed, 27 warnings
```

## 第十二轮优化：关系表首次初始化与渠道转移校准

### 1. 空 `channel_manager_area` 首次初始化不触发二轮重采

问题：

```text
channel_manager_area 刚创建时是空表；
第一轮 observed_graph 会看到大量 CHANNEL_MANAGER -> CHANNEL；
如果直接和空 current_graph 比较，会把所有关系都当作 added_relations；
这会触发一次不必要的二轮重采。
```

优化后：

```text
如果 channel_manager_area 表存在但没有任何记录：
  本轮作为 relation_bootstrap；
  暂不比较 CHANNEL_MANAGER -> CHANNEL 关系差异；
  不因为“全量新增关系”触发二轮重采；
  但最终返回 structure_changed=True，确保最终事务会写入关系表。
```

效果：

```text
首次初始化关系表：
  采集 1 轮即可成功；
  最终事务仍会写入 channel_manager_area；
  下一轮开始具备关系差异监控能力。
```

### 2. 渠道从经理 A 转到经理 B 的关系校准

新增测试覆盖：

```text
原关系：
  M001 -> C001

本轮观察：
  M002 -> C001
```

预期：

```text
area.CHANNEL(C001) 不禁用；
channel_manager_area(M001, C001) disabled；
channel_manager_area(M002, C001) enabled。
```

这次也修正了一个边界：

```text
如果某个 CHANNEL_MANAGER 本轮被 GRID 确认存在，
但它没有任何 CHANNEL observation，
也要把它视为“本轮已校准过的 manager”；
因此它历史上的旧渠道关系可以被禁用。
```

否则会出现：

```text
渠道已从 M001 转到 M002；
但 M001 -> C001 旧关系仍保持 enabled。
```

### 3. 验证结果

```text
dashboard 相关测试：34 passed
全量测试：188 passed, 27 warnings
```

## 第十三轮优化：结构变化摘要输出

### 1. 背景

结构收缩、manager 增减、渠道转移都已经可以被处理，但线上排查时仍需要快速知道：

```text
这轮到底变了什么？
影响哪些 GRID？
用了 affected_grid 还是 full 重采？
是正常 relation bootstrap，还是实际结构漂移？
```

如果只看日志和 rows，定位成本较高。

### 2. 新增 `structure_change_summary`

新增摘要函数：

```python
summarize_structure_changes(...)
```

`collect_validate_metric_rows_simple(...)` 返回中新增：

```text
structure_change_summary
```

内容包括：

```text
changed
retry_strategy
relation_bootstrap
affected_grid_codes
counts:
  added_targets
  removed_targets
  changed_targets
  added_relations
  removed_relations
  removed_areas
samples:
  每类变化最多保留 10 条样例
```

示例：

```json
{
  "changed": true,
  "retry_strategy": "affected_grid",
  "relation_bootstrap": false,
  "affected_grid_codes": ["AQ701"],
  "counts": {
    "removed_targets": 1,
    "added_relations": 0,
    "removed_relations": 0,
    "removed_areas": 0
  }
}
```

### 3. Pipeline 透传与日志

`execute_dashboard_pipeline(...)` 会把摘要放入：

```text
result["sync"]["structure_change_summary"]
```

当 `changed=true` 时，会额外输出一条结构变化摘要日志：

```text
驾驶舱结构变化摘要 batch_no=... summary=...
```

这样线上排查时不需要重新比对完整 rows。

### 4. 测试覆盖

新增/增强覆盖：

```text
relation bootstrap:
  changed=true
  relation_bootstrap=true
  added_relations 不计入漂移

manager shrink:
  affected_grid_codes=["AQ701"]
  removed_targets=1
  samples.removed_targets 包含被删除 manager
```

### 5. 验证结果

```text
dashboard 相关测试：34 passed
全量测试：188 passed, 27 warnings
```

## 第十四轮优化：结构变化摘要写入数据库

### 1. 背景

Prefect 日志不适合长期排查，结构变化摘要只在返回结果和日志中不够方便。

本轮将摘要持久化到：

```text
collection_run.structure_change_summary
```

字段内容是 JSON 文本。

### 2. 数据库迁移

新增迁移：

```text
migrations/dashboard/versions/20260616_0014_add_structure_change_summary.py
```

字段：

```sql
ALTER TABLE collection_run
ADD COLUMN structure_change_summary TEXT NULL;
```

真实库已执行：

```text
revision: 20260616_0014
collection_run.structure_change_summary: exists
```

### 3. 写入时机

写入位置：

```text
execute_dashboard_pipeline(...)
```

在最终写指标的同一个数据库事务中写入：

```text
collection_run.structure_change_summary = json.dumps(structure_change_summary)
```

因此结构摘要和本批次指标提交保持一致：

```text
如果最终事务失败：
  摘要不会单独提交；

如果最终事务成功：
  摘要和 metric_* 一起可查。
```

历史批次该字段为空是正常的，下一次采集成功后会自动写入。

### 4. 查询示例

查看最近批次：

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

查看有结构变化的批次：

```sql
SELECT
  batch_no,
  finished_at,
  structure_change_summary
FROM collection_run
WHERE structure_change_summary LIKE '%"changed": true%'
ORDER BY id DESC;
```

### 5. 测试覆盖

新增/调整：

```text
CollectionRun ORM 增加 structure_change_summary 字段；
MySQLCollectionRunStore serialize/update/get 支持该字段；
测试临时 collection_run 表同步新增字段。
```

验证结果：

```text
相关测试：33 passed
全量测试：188 passed, 27 warnings
```

## 第十五轮优化：关系缺失阈值、SQLite warning 清理、当前版本文档

### 1. `CHANNEL_MANAGER -> CHANNEL` 关系缺失阈值

新增配置：

```json
"relation_missing_disable_threshold": 2
```

普通关系缺失不再第一次就禁用：

```text
第一次缺失：
  missing_count + 1
  enabled 保持 true

连续缺失达到阈值：
  enabled = false
```

但两种强信号仍立即校准：

```text
manager 被确认删除：
  立即禁用该 manager 下关系

CHANNEL 同轮已经出现在另一个 manager 下：
  立即禁用旧 manager 关系
  启用新 manager 关系
```

这样兼顾了平台偶发漏返回和真实结构变化的及时性。

### 2. SQLite date/datetime warning 清理

此前测试中直接向 SQLite 传入 Python `date` / `datetime` 对象，会触发 Python 3.12 的 adapter warning。

本轮调整：

```text
tests/test_dashboard_retention_service.py
tests/test_dashboard_query_service.py
```

测试插入时改为传入 ISO 字符串。

结果：

```text
全量测试 warning 清零
```

### 3. 当前版本说明文档

新增：

```text
docs/dashboard当前版本说明.md
```

该文档只描述当前有效实现，包括：

```text
采集边界
核心表
主流程
重采策略
关系缺失阈值
空关系表初始化
结构变化摘要查询
当前配置项
当前测试状态
```

历史方案、修复推演和细节记录继续保留在：

```text
docs/dashboard收缩流程修复记录.md
docs/dashboard完整收缩流程图.md
docs/dashboard数据库流程与表结构.md
```

### 4. 验证结果

```text
python -m pytest -q
189 passed
```
