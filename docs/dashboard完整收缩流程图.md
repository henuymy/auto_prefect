# 数据驾驶舱完整收缩流程图

下面流程图描述的是按推荐目标收缩后的完整链路，覆盖会话、采集、校验、结构漂移、重采、最终事务和查询展示。

```mermaid
flowchart TD
    A["触发入口<br/>Prefect 定时 / API 手动"] --> B["创建 collection_run<br/>status = RUNNING"]
    B --> C["获取 dashboard_collection.lock"]
    C --> D["Session 探活<br/>city_ops / Uaptoken"]
    D --> E{"会话有效？"}

    E -- 否 --> F["写失败报告<br/>collection_run = FAILED"]
    F --> Z["结束"]

    E -- 是 --> G["加载当前数据库结构<br/>request_target / area_map / indicator"]
    G --> H["并发请求平台"]
    H --> I["采集结果<br/>rows<br/>structure_observations<br/>recoverable_errors"]

    I --> J["首轮区域与指标校验<br/>基于当前 area_map"]
    J --> K{"校验通过？"}

    K -- 是 --> L{"存在结构漂移？"}
    L -- 否 --> M["不消费 observation<br/>不更新 area/request_target"]
    L -- 是 --> N["异常状态<br/>校验通过但存在漂移证据<br/>进入人工诊断或失败"]

    N --> F

    K -- 否 --> O["判断失败原因"]
    O --> P{"是否确认结构漂移？"}

    P -- 否 --> Q["非结构问题<br/>请求失败 / 平台漏数 / 指标异常 / 覆盖不完整"]
    Q --> F

    P -- 是 --> R["消费 observation<br/>生成候选结构图<br/>candidate_area_map<br/>candidate_request_targets<br/>candidate_manager_channel_relations"]
    R --> S{"允许重采？"}

    S -- 否 --> F
    S -- 是 --> T["方案 A 局部重采<br/>只请求受影响 GRID<br/>和候选 CHANNEL_MANAGER"]
    T --> U["第二轮采集结果<br/>rows<br/>structure_observations<br/>recoverable_errors"]
    U --> V["合并结果后完整校验<br/>未受影响用第一轮<br/>受影响子树用第二轮"]
    V --> W{"再次通过？"}

    W -- 否 --> F
    W -- 是 --> X["标记本批次有效<br/>structure_changed = true"]

    M --> Y["最终 MySQL 短事务"]
    X --> Y

    Y --> Y1{"结构是否变化？"}
    Y1 -- 否 --> Y2["写 metric_snapshot<br/>写 metric_current / metric_acc<br/>collection_run = SUCCESS"]
    Y1 -- 是 --> Y3["写 area<br/>写 request_target<br/>写 channel_manager_area<br/>回填 area_id<br/>写 metric_snapshot<br/>写 metric_current / metric_acc<br/>collection_run = SUCCESS"]

    Y2 --> AA["提交事务"]
    Y3 --> AA["提交事务"]

    AA --> AB["FastAPI 查询最新成功数据"]
    AB --> AC["React 驾驶舱展示"]
    AC --> Z["结束"]
```

核心边界：

```text
失败时只写运行状态和失败报告；
成功时才在最终短事务里写正式结构和正式指标。
```

当前结构边界：

```text
request_target:
  CITY / BRANCH / GRID / CHANNEL_MANAGER

area:
  CITY / BRANCH / GRID / CHANNEL

channel_manager_area:
  CHANNEL_MANAGER -> CHANNEL
```

关键规则：

```text
不请求 CHANNEL；
GRID 响应用于发现 CHANNEL_MANAGER；
CHANNEL_MANAGER 响应用于采集 CHANNEL 指标，并同步 manager-channel 关系；
第二轮必须完整通过后，才允许提交 area / request_target / channel_manager_area / metric_*。
```

代码中的结构图对象：

```text
StructureGraph
  areas:
    CITY / BRANCH / GRID / CHANNEL
  targets:
    CITY / BRANCH / GRID / CHANNEL_MANAGER
  edges:
    GRID -> CHANNEL_MANAGER
    CHANNEL_MANAGER -> CHANNEL
```

变化检测：

```text
current_graph = load_current_structure_graph(...)
observed_graph = build_observed_structure_graph(...)
graph_diff = diff_structure_graph(current_graph, observed_graph)
```

只要 `graph_diff` 中出现新增、减少、改名、关系变化，就会触发候选结构重采。

重采策略：

```text
默认推荐方案 A：
  日常第一轮按当前 request_target 正常采集；
  如果 graph_diff 只影响部分 GRID，则第二轮只重采受影响 GRID 子树。
  最终 rows 由第一轮未受影响部分 + 第二轮受影响子树合并。

低峰校准可选方案 C：
  先请求所有 GRID 做结构探测；
  再按最新候选 request_target 正式采指标。
```

选择原因：

```text
渠道变化通常很少；
日常采用局部重采，请求量最小；
低峰再做全量结构探测，补充长期结构校准。
```
