# Dashboard 相关对话整理

## 1. Dashboard 采集流程、代码入口与数据库表结构

### 用户
理解下关于dashboard相关代码及流程，连接dashboard数据库，获得数据库的表格式，并总结到md中

### 助手
已完成梳理，并把结果写到 `docs/dashboard数据库流程与表结构.md`。

我做了两件事。第一，沿代码把 dashboard 的读写流程串起来了：API 入口在 `backend/routers/dashboard.py`，数据库连接在 `infrastructure/dashboard_mysql.py`，查询主逻辑在 `services/dashboard_query_service.py`，采集流程则经过 `flows/dashboard_metric_flow.py`、`services/dashboard_batch_runner.py`、`services/dashboard_collection_orchestrator.py`。第二，我实际连上了 dashboard MySQL，确认当前库是 `dashboard`，版本 `8.4.8`，真实表有 `alembic_version`、`area`、`collection_run`、`dashboard_wide`、`indicator`、`metric_acc`、`metric_current`、`metric_snapshot`、`request_target`。

文档里已经包含表用途、关键字段、接口到表的映射，以及当前连接目标的非敏感信息。若要继续深入，建议优先看 `get_dashboard_overview_fast()` 和 `get_drill_down()`，最能说明前端页面现在到底是走宽表还是走明细表。

---

## 2. 当前采集流程，以及 `area` 表和请求表如何从采集数据同步

### 用户
现在这个采集流程是什么样的，area表和请求表是如何同步的从采集的数据中

### 助手
现在这套采集流程，主入口是 `tasks/dashboard_tasks.py`。`mode=REALTIME` 走 `services/dashboard_pipeline.py` 的 `execute_dashboard_pipeline()`；`DAY_ACC` 和 `MONTH` 现在也复用这条通用管道，只是 `period_type` 和 `run_type` 不同。整体顺序是：

1. `dashboard_batch()` 先做批次初始化、加锁、会话探活、连 dashboard MySQL，并从 `request_target` 读本轮采集目标、从 `indicator` 读启用指标。
2. `create_simple_fetcher()` 创建请求器后，`collect_validate_simple()` 开始采集。
3. 采集本身按 `request_target` 并发请求，每个目标返回一批表格数据，再抽成标准行 `{level_type, area_code, area_name, parent_request_code, 指标...}`。
4. 第一轮采完后，先用采集结果同步 `area`，再做区域覆盖校验；如果校验没过，就从 `area` 反推刷新 `request_target`，然后按新的目标表再采一轮。
5. 校验通过后，实时批次写 `metric_snapshot + metric_current`，累计批次写 `metric_acc`，最后再尝试同步一次 `request_target` 并清理过期数据。

`area` 表不是从单独的结构接口同步，而是直接从“采集回来的指标数据行”里提取出来的。核心逻辑是 `sync_areas_from_acc()`：

- 新出现的区域就新增到 `area`
- 已有区域就更新名称、重置 `missing_count`、刷新 `last_seen_at`
- 当前库里原本启用、但这轮没再出现的，就直接 `enabled=False`

所以这里的关键点是：`area` 的来源就是采集结果本身，不依赖静态配置，也不是从 `request_target` 倒灌回去。

`request_target` 表则是从 `area` 表二次派生出来的，不是直接从采集响应里写的。逻辑是 `sync_request_targets_from_areas()`：

- 先读所有启用的 `area`，限定层级 `CITY/BRANCH/GRID`
- 再读所有启用的 `request_target`
- 对每个 `area`，把 `level_type` 映射成同名 `target_type`
- 不存在的就新建 `request_target`
- 已存在的就同步名称和 `area_id`
- 已有 target 但 `area` 不再存在的，就禁用 target

这意味着当前关系是单向的：

`采集数据 -> area -> request_target -> 下一轮采集目标`

还有一个实现上的细节：`collect_metric_rows()` 在采集时还能从 `GRID`/`CHANNEL_MANAGER` 响应里提取子级结构观察值 `extract_structure_observations()`，但当前主流程没有真正消费这些结构观察结果，`orchestrated["structure_sync"]` 现在基本是空壳。所以目前真正生效的同步依据，还是指标数据行里的 `level_type/area_code/area_name`。

---

## 3. 请求响应的结构

### 用户
请求响应的结构

### 助手
当前采集接口返回的是一个 JSON 对象，代码只严格依赖两层结构：

```json
{
  "reCode": "0000",
  "reMsg": "success",
  "result": {
    "tableData": [
      {
        "areaCode": "AQ",
        "areaName": "中原区",
        "some_indicator_code": "3370"
      }
    ]
  }
}
```

核心依赖是：

- 成功码看顶层 `reCode`，允许值是 `"0"` 或 `"0000"`
- 数据明细固定从 `result.tableData` 读取，必须是数组
- 每一行至少会用到 `areaCode`、`areaName`，其余就是指标字段，字段名直接就是请求里的指标编码

按当前实现，不同请求目标对 `tableData` 的解释不一样：

1. `CITY / BRANCH / GRID / CHANNEL`
   - 要求 `tableData` 里必须有且只有一条“本级汇总行”
   - 即 `row.areaCode == target.target_code`

2. `CHANNEL_MANAGER`
   - 把 `tableData` 视为“经理下面的渠道列表”
   - 会跳过与目标自身同码的那一行，保留其余子渠道

例如目标是 `M001`，允许响应这样：

```json
{
  "reCode": "0000",
  "result": {
    "tableData": [
      { "areaCode": "M001", "areaName": "经理1", "metric": 10 },
      { "areaCode": "C001", "areaName": "渠道1", "metric": 3 },
      { "areaCode": "C002", "areaName": "渠道2", "metric": 4 }
    ]
  }
}
```

最终会被转换成标准采集行：

```json
[
  {
    "level_type": "CHANNEL",
    "area_code": "C001",
    "area_name": "渠道1",
    "parent_request_code": "M001",
    "metric": 3
  },
  {
    "level_type": "CHANNEL",
    "area_code": "C002",
    "area_name": "渠道2",
    "parent_request_code": "M001",
    "metric": 4
  }
]
```

还有几个约束：

- `reCode=1101/401/403` 或 `reMsg` 里有“登录/超时”，会被判为 session 失效
- `reCode=1104` 被当作可重试错误
- 行里如果没有 `areaCode` 或 `areaName`，同步 `area` 时这条记录基本就失去价值

---

## 4. 请求表如何从 `area` 表获得

### 用户
那请求表是怎么从area表中获得的，不要写 (/C:/Users/yuyu/Desktop/项目/自动通报/services/dashboard_sync_service.py:60)，不然很混乱排版

### 助手
请求表 `request_target` 不是直接从接口响应里生成的，而是从已经同步好的 `area` 表二次派生出来的。

当前逻辑是这样的：

1. 先采集到指标数据行，每一行里会带 `level_type`、`area_code`、`area_name` 等信息。
2. 用这些采集行去更新 `area` 表：
   - 新出现的区域就新增到 `area`
   - 已有区域就更新名称、刷新 `last_seen_at`
   - 本轮没再出现的启用区域就禁用
3. 再从 `area` 表反推 `request_target`，这里只看启用状态的 `CITY`、`BRANCH`、`GRID` 三层区域，不看 `CHANNEL`。

`request_target` 的生成规则本质上是“按 `area` 复制一份可采集目标”：

- `CITY` 区域变成 `CITY` 目标
- `BRANCH` 区域变成 `BRANCH` 目标
- `GRID` 区域变成 `GRID` 目标
- `CHANNEL` 不会直接变成请求目标

同步时会做三类动作：

- 新建：如果某个启用的 `area` 在 `request_target` 里还没有同类型同编码的记录，就创建一条新的 target
- 更新：如果 target 已存在，就把名称和 `area_id` 对齐到最新的 `area`
- 禁用：如果某个启用 target 对应的 `area` 已经不在启用区域集合里了，就把这个 target 禁掉

为什么不直接同步 `CHANNEL`：因为当前采集设计里，渠道一般不是作为独立固定 target 直接请求的，而是通过上层目标下钻出来的。代码里明确只把 `CITY/BRANCH/GRID` 同步进请求表，`CHANNEL` 被排除掉了。

父子关系怎么来的：同步 `request_target` 时，还会顺手给 target 建父子关系。

规则是硬编码推断的：

- `BRANCH` 的父级固定找 `CITY`
- `GRID` 的父级通过编码前缀去找对应 `BRANCH`
- 所以 `request_target.parent_target_id` 其实是从 `area` 的层级和编码规则推出来的，不是从接口单独返回的一张结构表直接灌进去的

所以整条链路可以简化成一句话：

`采集结果先落 area，request_target 再从启用的 area 里筛出 CITY/BRANCH/GRID 自动同步出来，作为下一轮采集目标。`

---

## 5. `CHANNEL_MANAGER` 的来源，以及为什么当前设计会重采

### 用户
继续

### 助手
`CHANNEL_MANAGER` 之所以不在“`area -> request_target` 自动同步链”里，是因为它和 `CITY/BRANCH/GRID` 的来源不一样。

简单说：

- `CITY/BRANCH/GRID` 是正式区域，能直接落到 `area` 表
- `CHANNEL_MANAGER` 不是正式区域，而是“采集路径节点”
- 所以它不会进 `area`，而是单独进 `request_target`

当前系统里 `request_target` 实际混了两类东西：

1. 正式区域型目标：`CITY / BRANCH / GRID`
2. 采集路径型目标：`CHANNEL_MANAGER`

所以你会看到一个关键差别：

- `CITY/BRANCH/GRID` 的 `request_target.area_id` 是有值的
- `CHANNEL_MANAGER` 的 `request_target.area_id` 通常是 `None`

### 用户
channel_manager是从excel中固定获取？当前代码里 CHANNEL_MANAGER 更新不自动，会带来什么实际问题，以及为什么你会看到“第一次采不全、同步后重采一次”的设计。

### 助手
对，按当前代码来看，`CHANNEL_MANAGER` 主要还是从 Excel 层级表固定导入的，不是像 `CITY/BRANCH/GRID` 那样由采集结果自动维护。

可以拆成两部分看：

#### `CHANNEL_MANAGER` 现在是不是固定从 Excel 来的？
基本是。

当前系统里有两条不同来源：

- 正式区域：`CITY / BRANCH / GRID / CHANNEL`
  - 来自采集结果
  - 会进入 `area`
  - `CITY / BRANCH / GRID` 还会继续自动同步成 `request_target`

- 采集路径节点：`CHANNEL_MANAGER`
  - 不进入 `area`
  - 主要通过层级 Excel 导入到 `request_target`
  - 用来作为“请求入口”，再往下采出 `CHANNEL`

所以现在的 `CHANNEL_MANAGER` 更像“配置好的采集路径”，不是“运行时自动生长出来的正式区域”。

#### `CHANNEL_MANAGER` 不自动更新，会带来什么问题？
会带来 4 类典型问题：

- 经理变了：旧经理编码还在 `request_target`，新经理没进来，请求会打到旧入口
- 经理挂靠的网格变了：`parent_target_id` 还是旧关系，导致渠道路径错位
- 新增了经理：`request_target` 不会凭空多出新的 `CHANNEL_MANAGER`
- 经理被撤销/失效：旧的 `CHANNEL_MANAGER` 还留在请求表里，可能持续失败或返回空数据

本质后果就是：`CHANNEL_MANAGER` 一旦和真实平台结构脱节，最直接的后果就是渠道层数据不完整。

#### 为什么会有“第一次采不全、同步后重采一次”的设计？
因为当前系统承认一个现实：启动采集时，数据库里的目标结构可能已经落后于平台真实结构。

所以设计成“两段式修正”：

1. 第一轮采集：先用现有 `request_target` 去采，目的不是一次成功，而是先拿到“平台当前长什么样”的事实数据。
2. 同步 `area`：把 `area` 修正成更接近当前真实结构。
3. 校验覆盖率：把“本轮采回来的区域集合”跟“当前启用的 area 集合”做比对。
4. 再同步 `request_target`，然后重采：因为 `CITY/BRANCH/GRID` 能从 `area` 反推出来，所以系统会根据更新后的 `area` 重建这三层请求目标，再跑第二轮采集。

这个“重采一次”的本质不是补重试，而是：

`先用旧结构探路，再根据探回来的真实区域结构修正请求目标，然后用新结构再采一次。`

但这里还不能彻底解决 `CHANNEL_MANAGER` 问题，因为第二轮自动修正只能稳定修正 `CITY/BRANCH/GRID`，修不到 `CHANNEL_MANAGER`。

所以这套设计能缓解“上层区域结构过期”，但不能彻底修复“经理层路径过期”。

---

## 6. 如何解决这个问题，能否不依赖 Excel

### 用户
如何解决这个问题呢？能否从请求的结果中，同时更新area表和请求表，不依赖excel

### 助手
可以，而且这是更合理的方向。核心思路就是把现在“只从结果更新 `area`，但 `CHANNEL_MANAGER` 还靠 Excel”改成：从每次请求返回的数据里，同时提取“正式区域”和“采集路径结构”，分别更新 `area` 和 `request_target`，这样就能逐步摆脱 Excel 依赖。

建议改成下面这套模型：

- `area` 只维护正式区域：`CITY / BRANCH / GRID / CHANNEL`
- `request_target` 只维护可请求节点：`CITY / BRANCH / GRID / CHANNEL_MANAGER`
- 每次请求响应时，同时抽两类信息：
  - 区域事实：当前返回的本级或子级 `area_code / area_name / level_type`
  - 路径事实：父请求目标是谁、返回了哪些下一级节点

### 怎么做

1. 保留现在的 `area` 同步，但扩展它
   - 不要只看最终指标行，还要看每次请求返回的原始列表，把里面出现的正式区域都记录下来
   - 请求 `CITY` 能看到 `BRANCH`
   - 请求 `BRANCH` 能看到 `GRID`
   - 请求 `GRID` 能看到 `CHANNEL_MANAGER`
   - 请求 `CHANNEL_MANAGER` 能看到 `CHANNEL`
   - 其中 `BRANCH / GRID / CHANNEL` 进入 `area`，`CHANNEL_MANAGER` 不进 `area`

2. 新增“结构观察结果”作为同步输入
   - 每次请求结束，不光产出指标行，还产出一份结构观察
   - 例如：`GRID -> CHANNEL_MANAGER`
   - 或：`CHANNEL_MANAGER -> CHANNEL`
   - 然后把这份观察数据作为正式同步依据，而不是只在采集时临时用一下

3. 用结构观察自动更新 `request_target`
   - `request_target` 不再从 Excel 导入，而是从“结构观察”里增量维护
   - 看到 `CITY -> BRANCH`：确保 `BRANCH` target 存在
   - 看到 `BRANCH -> GRID`：确保 `GRID` target 存在
   - 看到 `GRID -> CHANNEL_MANAGER`：确保 `CHANNEL_MANAGER` target 存在，并挂到对应 `GRID`
   - `CHANNEL` 不写进 `request_target`

最关键的一点：同步不能按“本轮没看到就立刻禁用”。

要改成软删除/衰减机制：

- 每次看到节点：`enabled = true`、`last_seen_at = now`、`missing_count = 0`
- 每次应该出现但没看到：`missing_count += 1`
- 只有当连续多轮未出现，或者超过一定过期时间，才禁用

例如：
- `CHANNEL_MANAGER` 连续 3 轮未出现再禁用
- `CHANNEL` 连续 2 轮未出现再禁用

推荐流程：

1. 从 `request_target` 读取当前可采集节点
2. 执行采集，并发请求所有 target，同时得到：
   - 指标结果
   - 结构观察
3. 同一批次结束后：
   - 用正式区域结果更新 `area`
   - 用结构观察更新 `request_target`
4. 区域覆盖校验
5. 必要时只局部补采受影响节点，而不是整批重跑

### 比现在更好的地方

- 不依赖 Excel
- `CHANNEL_MANAGER` 自动生长
- 结构变化能自愈
- 不需要整批重采两次，可以升级成局部补采

### 风险与建议

- 平台返回不一定稳定，要先确认每种 target 的响应里，哪些下级结构是真能稳定拿到的
- 不能把一次异常当结构变更，必须加 `missing_count / last_seen_at / 多轮确认`
- 父子关系要优先以“观察到的父请求”为准，不要只靠编码前缀猜父级
- 最好保留一个“种子入口”，即便不靠 Excel，也建议保留最上层稳定入口（如 `CITY` 或少量 `BRANCH`）

最推荐的落地方式不是一步删掉 Excel，而是分 3 步：

1. Excel 改为“初始化种子”
2. 请求结果驱动增量同步
3. 自动同步稳定后，彻底移除 Excel 对经理层的日常依赖

结论：能做到，而且应该这么做。正确做法不是“直接从指标结果硬推请求表”，而是从请求响应里同时抽取正式区域事实和请求路径事实：前者更新 `area`，后者更新 `request_target`。这样 `CHANNEL_MANAGER` 就能自动维护，Excel 只需要保留为初始化或兜底，不再是主数据源。
