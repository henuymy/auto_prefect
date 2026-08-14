# 自动通报系统

基于 Prefect、FastAPI 和 React 的自动化报表通报与数据驾驶舱。系统负责登录业务平台、采集或下载报表、比对并更新 Excel 模板、发送企业微信通知，同时提供任务配置、运行日志和驾驶舱查询界面。

## 运行链路

```text
Prefect Flow
  → 登录或复用会话
  → 采集/下载业务数据
  → 校验、比对并更新模板
  → 生成截图与通知内容
  → 企业微信发送
  → 成功后提交正式模板
```

驾驶舱采集链路独立写入 MySQL，经 FastAPI 提供查询接口，由 React 前端展示实时值、累计值、历史数据和目标完成情况。

## 目录结构

```text
.
├── backend/          # FastAPI 应用、路由和后台服务
├── frontend/         # React + Vite 配置中心与数据驾驶舱
├── flows/            # Prefect Flow 编排入口
├── tasks/            # Prefect Task 封装
├── services/         # 登录、采集、比对、通知和驾驶舱业务逻辑
├── infrastructure/   # MySQL、文件存储、Excel、企业微信等适配器
├── models/           # 领域模型与 SQLAlchemy 模型
├── migrations/       # Alembic 数据库迁移
├── config/           # 模块、报表、任务和驾驶舱配置
├── prefect.yaml      # Session Keeper、驾驶舱等系统级 Prefect 部署声明
├── scripts/          # 环境、服务、迁移和运维脚本
├── tests/            # Python 自动化测试
├── utils/            # 配置、日期和请求解析工具
└── templates/        # 本地业务模板，不纳入版本控制
```

运行时 Cookie、会话、日志和临时报表位于 `C:\AutoNotifyRuntime`，不要提交、公开或随意清空。当前 19 个系统和通报 Deployment 统一声明在 `prefect.yaml`；前端新增或调整通报调度后，必须同步更新该文件，避免环境重建时遗漏调度。

## 环境要求

- Windows 10/11
- Conda `base` 环境，Python 3.13
- NVM 管理的 Node.js 20 LTS
- MySQL 8（仅驾驶舱功能需要）
- Prefect Server 3.7（仅本地运行 Prefect 服务与调度需要）
- Microsoft Edge（自动登录与网页采集需要）；涉及 Excel COM 的比对、模板更新和截图功能还需 Microsoft Excel

## 安装

在 Conda `base` 环境安装锁定的 Python 开发依赖：

```powershell
conda activate base
python -m pip install -r requirements-dev.lock
```

若默认清华 PyPI 镜像返回 `HTTP 403`，可临时改用官方 PyPI：

```text
python -m pip install -r requirements-dev.lock -i https://pypi.org/simple
```

初始化脚本只安装 Python 依赖，不会安装前端 npm 依赖。即使脚本最后输出“完成”，仍应确认基础自检出现 `smoke_ok`；依赖安装或自检报错时不能视为初始化成功。

通过 NVM 安装并启用前端所需的 Node.js 20 LTS，再安装前端依赖：

```powershell
nvm install 20.20.1
nvm use 20.20.1
npm --prefix frontend ci
```

也可以使用项目初始化脚本完成 Python 依赖、运行目录和本机组件检查：

```powershell
pwsh -File scripts/setup_windows_env.ps1
```

所有私密 JSON 配置统一保存在被 Git 忽略的 `config/runtime.local.json`。其中的运行目录、Prefect、驾驶舱 MySQL、监控密钥、模块凭据和任务差异分别按固定分区维护；切勿把账号、Cookie、Webhook 或数据库密码写入受版本控制的文件。

### 为什么忽略本地配置

`.gitignore` 会忽略 `config/runtime.local.json` 和其他本地凭据文件。该文件会因电脑、环境或账号不同而变化，并可能包含数据库连接信息、Prefect API 地址、Webhook、访问令牌和 Cookie；提交它会泄露凭据，也会让其他环境错误继承本机配置。

应提交不含真实凭据的 `*.example.json` 或普通配置模板；每台机器自行创建自己的 `config/runtime.local.json`。不要使用 `git add -f` 强制提交被忽略的本地配置。

## 依赖文件职责

| 文件 | 职责 |
| --- | --- |
| `pyproject.toml` | Python 项目的直接运行依赖、开发可选依赖和工具配置的唯一声明来源。 |
| `requirements.txt` | Python 运行时安装入口，引用 `requirements.lock`。 |
| `requirements.lock` | 由 `pyproject.toml` 解析生成的 Python 运行时精确版本清单。 |
| `requirements-dev.lock` | 由 `pyproject.toml` 的开发额外依赖解析生成，供开发、测试和质量检查安装。 |
| `frontend/package.json` | 前端的直接依赖、开发依赖与 npm 脚本声明。 |
| `frontend/package-lock.json` | npm 解析出的前端精确版本清单，保证不同机器安装结果一致。 |

锁文件会记录直接依赖及其传递依赖，因此行数通常远多于 `pyproject.toml` 和 `frontend/package.json` 中列出的直接依赖；不要手动逐项编辑锁文件。

## 配置

- `config/reports/*.json`：通报业务配置，前端正式保存的位置。
- `config/task_defaults/notify.json`：通报 Flow 的公共步骤、模块配置和默认运行参数。
- `config/tasks/*.json`：通报任务的报表标识与少量差异覆盖；运行时自动合并公共默认值。
- `config/modules/*.json`：登录、下载、比对、模板和发送模块配置。
- `config/dashboard/session.json`：驾驶舱采集、数据库、并发与留存策略。
- `scripts/tools/dashboard/`：驾驶舱导入、导出、审计与迁移等低频工具。

当开发库与生产库位于同一 MySQL 服务实例时，两边必须在 `config/runtime.local.json` 的 `dashboard.session_overrides` 中分别配置不同的 `collection_database_lock_name` 和 `partition_database_lock_name`，例如名称后缀使用 `_dev`、`_prod`；同一环境中所有机器必须保留相同锁名，避免同一套数据被并发采集或维护。

### 渠道指标排除

驾驶舱右上角“设置”中的“渠道指标排除”用于维护“某个渠道的某个指标不纳入统计”的规则。选择渠道、已启用且结果落库（`STORE`）的指标、生效日期和可选失效日期后保存；规则可预览受影响的渠道经理、网格、分公司和市级路径，也可以取消，但不会物理删除审计记录。

规则不改变上游请求、组织树校验、原始指标事实或目标值。每次采集仍按“采集 -> 结构校验 -> 绑定节点与公式计算 -> 生成排除口径 -> 写入事实”的顺序执行。原始值继续写入 `metric_current`、`metric_snapshot` 与 `metric_acc`；同批次的有效值写入 `metric_caliber_override`，其中渠道自身的被排除指标状态为 `EXCLUDED`，页面显示“未纳入统计”而不是数值 `0`。其祖先节点按被排除渠道的贡献扣减；当前公式只支持加减/线性系数，因此可一并扣减受影响的组合指标。目标值保持原值，不会因排除规则调整。

新规则从生效日期后的下一次成功采集起生成完整的批次重算结果，不需要新建数据库或重算全部历史事实。若需要立即刷新当前汇总，可受控地触发一次驾驶舱采集；不要通过直接修改 `metric_current`、`metric_snapshot` 或 `metric_acc` 代替采集。

| 表 | 职责 |
| --- | --- |
| `channel_indicator_exclusion` | 保存渠道、指标、生效区间、状态、原因和审计字段；同一渠道指标的有效日期区间不能重叠。 |
| `metric_caliber_override` | 保存某一采集批次中渠道 `EXCLUDED` 状态及其祖先/组合指标的有效值，不覆盖原始事实。 |

管理接口为 `GET/POST /api/dashboard/channel-indicator-exclusions`、`POST /api/dashboard/channel-indicator-exclusions/preview`、`PATCH/DELETE /api/dashboard/channel-indicator-exclusions/{id}`。数据库升级后先验证 `python -m alembic -c .\alembic_dashboard_v2.ini current` 已到 `head`，再使用管理界面保存规则。

### 驾驶舱目标草稿与考核版本

驾驶舱将“日常执行目标”和“用于考核的历史目标”分成两条独立链路。目标值设置页面只维护可编辑的**目标草稿**；版本记录页面只展示只读、可审计的**考核版本**。这样可以持续调整当前执行目标，同时保留每次发布时的考核依据。

| 对象 | 数据库状态 | 是否可改 | 用途 |
| --- | --- | --- | --- |
| 目标草稿 | `DRAFT` | 可以保存、导入和继续修改 | 作为实时看板与实时累计的候选执行目标。 |
| 当前实时目标 | `DRAFT` 且 `is_realtime=true` | 可以继续修改 | 当前业务日期命中的草稿会供实时查询使用。每个场景和目标周期只能由一份草稿担任这个角色。 |
| 考核版本 | `ACTIVE` 或 `RETIRED` | 不可以直接修改 | 发布时从草稿复制出的目标值，用于历史、累计和审计。 |

`NORMAL`（日常）和 `PK`（PK 赛）是两套隔离的目标口径；`DAY` 和 `MONTH` 也分别管理。`plan_name` 用于识别一组方案和审计谱系，`version_no` 是该组方案的版本号，`supersedes_plan_id` 指向被替代的已发布版本。草稿和已发布副本允许同为 `v1`，这是迁移后的有意设计，不能仅凭版本号跨状态或跨方案判断同一条记录。

`ACTIVE` 仍然有明确职责：它表示尚未被后续发布版本替代的考核版本；`RETIRED` 表示已被替代、但必须继续保留以供审计和历史日期查询的考核版本。历史查询会同时考虑 `ACTIVE` 与 `RETIRED`，并不是“只看 ACTIVE”。

#### 各页面使用什么目标

| 驾驶舱数据模式或接口 | 目标来源 | 说明 |
| --- | --- | --- |
| 实时、实时变化、实时矩阵、下钻 | 当前实时目标草稿 | 使用最新实时采集值与命中的 `DAY` 草稿。 |
| 实时累计 | 当前实时目标草稿 | 使用“前一日累计 + 当日实时”计算，完成率使用命中的 `MONTH` 草稿。 |
| 累计基线面板 | 当前实时目标草稿 | 只展示截至最近已完成采集日的 `DAY_ACC`，不含当天实时；目标仍按当前月目标草稿展示。 |
| 历史、历史矩阵、历史下钻 | 已发布考核版本 | 根据所选历史数据的业务日期匹配 `DAY` 考核版本。 |
| 累计日期查询（`/api/dashboard/acc`） | 已发布考核版本 | 累计实际值可从 `DAY_ACC` 读取，目标按累计日期匹配 `MONTH` 考核版本。 |

`DAY_ACC` 是累计实际值的数据周期，不是目标周期。真正的月度执行口径是“实时累计”：它必须显式传入 `target_period=MONTH, target_source=WORKING`，计算“前一日累计 + 当日实时”。旁边的“累计基线”只展示最近已完成采集日的 `DAY_ACC`，不含当天实时。累计日期查询传 `target_period=MONTH, target_source=ASSESSMENT`。实时查询找不到命中的实时草稿时直接返回空目标；不会改用已发布考核版本。目标缺失必须在目标值设置中补齐，不能通过历史版本兜底掩盖配置问题。

累计页面的日期下拉框来自 `GET /api/dashboard/acc/options`，只列出 `metric_acc` 中已经落库的 `period_type=DAY_ACC` 的 `stat_date`，不是按自然日补齐，也不会因为创建月目标草稿而生成日期。日累计任务在业务日结束后写入前一天的快照，因此 2026-08-04 最多显示已成功落库的 2026-08-03，不能期待当天的 2026-08-04 立即出现在历史累计列表。若调度运行成功但页面仍停在旧日期，先刷新页面或重新进入“累计”并点击“查询”；前端会重新读取日期选项。仍无日期时，再检查 `collection_run.run_type=DAY_ACC` 的 `SUCCESS`、`stat_date` 和 `metric_acc` 行数。目标草稿、累计实际快照和前端缓存是三个独立边界。

### 驾驶舱分阶段加载

单指标、默认组织范围且未开启任一层级“全部”时，驾驶舱使用 `GET /api/dashboard/staged` 分阶段读取数据。默认范围是所选分公司及其后代节点；下钻时以当前父节点为范围，不会额外读取其他分公司的渠道经理或渠道。多指标、“全部”范围和层级“全部”开关仍走原有查询链路，避免改变其既有展示与分页语义。

1. `stage=CORE&payload=VALUES` 先返回当前范围内的 `BRANCH`、`GRID`、`CHANNEL_MANAGER`，页面据此完成首屏渲染。
2. `stage=CHANNELS&payload=VALUES` 随后只返回 `CHANNEL`，前端按节点 ID 合并到已展示的数据中。
3. `payload=CHANGES` 对 `CORE` 和 `CHANNELS` 分别请求，响应只含 `{ id, changes }` 补丁；数值到达后再补齐变化窗口，不阻塞首屏。`CUMULATIVE` 不计算变化量，因此不发送此类请求。

接口的 `data_mode` 支持 `REALTIME`、`REALTIME_ACC`、`CUMULATIVE` 和 `HISTORY`。历史档位必须传 `as_of`，累计可传 `stat_date`；每个响应均带有 `data_version` 和 `config_version`。前端只合并与核心响应版本一致的后续阶段，版本变化时重新查询，不能把不同采集批次或配置版本的数据混在同一看板中。

单次阶段响应以 200 至 300 KB 为控制目标。新增节点字段、指标字段或变化窗口前，应以实际选定指标和组织范围测量响应体大小；渠道和变化量不能重新合并到首屏核心请求中。

#### 首次访问优化

实时模式首次进入时只启动首屏必要链路：先请求核心数据，核心响应到达后立即渲染；指标目录和渠道数据在后台并行补齐，变化量随后按阶段加载。指标目录请求在本次页面生命周期内复用，首次没有本地保存的单指标时，目录结果直接用于选择第一个指标，不再重复发起一次“全部指标查询后再查单指标”的请求。

历史范围和历史可选时间只在用户切换到“历史档位”时加载，累计日期选项也只在进入“累计”模式时加载。这样实时首屏不会被历史查询初始化阻塞，但切换历史或累计时仍会按需获取对应选项。开发环境的 `5174` 使用 Vite，首次加载还包含模块转换和浏览器缓存建立；正式环境应使用 `npm run build` 生成的静态资源，以避免把开发服务器开销带到生产环境。

#### 场景、周期与数据表边界

| 因素 | 允许的值/判断字段 | 对应表 | 作用 |
| --- | --- | --- | --- |
| 业务场景 | `NORMAL`、`PK`；`target_plan.scenario` | `target_plan` | 隔离日常目标和 PK 目标，查询必须显式传入，不能互相替代。 |
| 目标周期 | `DAY`、`MONTH`；`target_plan.period_type` | `target_plan`、`metric_target_value` | 决定目标数值按日还是按月解析；`DAY_ACC` 不是目标周期。 |
| 方案身份与版本谱系 | `plan_name`、`version_no`、`priority`、`supersedes_plan_id` | `target_plan` | 标识同一方案的修订链和覆盖优先级；版本号不是跨状态的全局 ID。 |
| 目标版本状态 | `DRAFT`、`ACTIVE`、`RETIRED`；`target_plan.status` | `target_plan` | `DRAFT` 可编辑；`ACTIVE/RETIRED` 只读并供历史审计。 |
| 实时资格 | `DRAFT` 且 `target_plan.is_realtime=true` | `target_plan` | 当前实时和实时累计唯一允许的目标来源；缺失时返回空目标，不回退。 |
| 目标生效区间 | `effective_from` 至 `effective_to`（含两端） | `target_plan` | 用业务日期命中方案，不按发布时间命中。 |
| 目标来源 | `WORKING` 或 `ASSESSMENT`（接口参数） | 查询服务 + `target_plan` | `WORKING` 只解析实时草稿；`ASSESSMENT` 只解析已发布版本，二者不得互换。 |
| 目标业务日期 | 实时取成功 Run 的 `stat_date`；历史/累计取所选日期 | `collection_run`、`metric_acc` | 只用于匹配 `effective_from/effective_to`，不能用发布时间代替。 |
| 目标明细 | `plan_id + node_id + indicator_id`、`target_value` | `metric_target_value` | 保存具体节点和指标的目标数值；只能跟随所属方案状态写入。 |
| 组织与指标维度 | `node_id`、`indicator_id` | `hierarchy_node`、`indicator` | 定义目标明细和实际事实的节点、指标范围；禁用节点/指标不参与查询。 |
| 当前实时事实 | 最新成功 Run 的 `stat_date`、当前值 | `collection_run`、`metric_current` | 实时页面的实际值来源；目标仍按实时草稿解析。 |
| 历史事实 | 采集时间和批次快照 | `collection_run`、`metric_snapshot` | 历史页面的实际值来源；目标按业务日期解析考核版本。 |
| 累计事实 | `period_type=DAY_ACC`、`stat_date` | `metric_acc` | 累计实际值来源；实时累计使用“前一日累计 + 当日实时”。 |

实时目标查询的完整命中条件是：`scenario`、目标 `period_type`、`DRAFT`、`is_realtime=true`，并且业务日期落在生效区间内。任一条件不满足，目标值就是空值；系统不会跨场景、跨周期、跨状态或跨生效区间选取其他方案。

#### 自定义指标与源指标边界

指标不是单一状态。源指标以 `indicator_type=SOURCE` 标识，组合指标以 `indicator_type=CUSTOM` 标识；`enabled` 表示是否独立展示，`storage_mode` 表示数据角色（`STORE` 为“结果落库”，`COMPONENT` 为“仅计算输入”），`indicator_formula_component` 表示是否被组合指标引用，`source_active`/`removed_at` 表示源指标是否已归档。这些维度相互独立，不能用“公式组件”同时表达数据角色和引用关系。

合法且常见的源指标组合是“独立展示 + 结果落库 + 被公式引用”：它既可在驾驶舱单独查看，也能参与组合公式。纯计算输入必须为“不独立展示 + 仅计算输入 + 被公式引用”；“独立展示 + 仅计算输入”无可查询的独立结果，服务端和界面都会拒绝该组合。已归档源指标不能再被新公式引用，但历史事实不会删除。顶部“被公式引用”筛选是关系筛选，与“独立展示”“结果落库”可以重叠，统计数量不能相加。

组合指标固定为“`CUSTOM` + 结果落库”，当前仅允许引用源指标，公式为源指标乘系数后的加权组合，不支持组合指标嵌套。公式编辑器只保存源指标和系数，不得修改源指标全局数据角色；依赖状态只显示“依赖正常”“依赖可用（不独立展示）”“已归档”或“待选择”。

#### 建表边界与历史冻结

当前目标体系不需要为日常/PK、日/月、实时/历史或草稿/考核版本分别建表。目标配置统一使用已有的 `target_plan`（方案身份、场景、目标周期、状态、版本和生效区间）与 `metric_target_value`（节点 × 指标目标值）；实际数据继续使用 `collection_run`、`metric_current`、`metric_snapshot` 和 `metric_acc`。因此，当前没有月目标草稿时，只需创建一条 `MONTH + DRAFT` 的方案记录并写入目标明细，不需要新增数据库表。

| 需求 | 新增表数量 | 处理方式 |
| --- | ---: | --- |
| 当前实时、实时累计、累计、历史查询 | 0 | 使用现有目标表和事实表，通过场景、周期、来源和业务日期解析。 |
| NORMAL/PK 或 DAY/MONTH 隔离 | 0 | 使用 `target_plan.scenario`、`target_plan.period_type`，禁止拆分成多套表。 |
| 草稿与考核版本隔离 | 0 | 使用 `target_plan.status` 和 `is_realtime`，发布时复制方案和值。 |
| 采集时永久绑定考核版本，后续修订绝不重算历史 | 1（可选） | 新增 `dashboard_assessment_snapshot`，保存 `collection_run_id`、`target_plan_id`、节点、指标、实际值、目标值和完成率；或者经评审后为事实表增加等价的考核版本绑定字段。 |

当前实现的历史完成率仍是查询时按业务日期解析 `ACTIVE/RETIRED`。未来生效日期之外的新版本不会影响旧日期；同一生效日期的修订可能改变该日期的查询结果。若业务要求彻底冻结，必须先实施上述 1 张结算快照表，再宣称历史结果不可重算。

#### 日常操作流程

1. 在驾驶舱点击“目标值设置”，新建草稿，填写方案名称、场景、目标周期、生效日期和优先级。
2. 在“目标草稿”中逐项填写目标值，或下载模板后导入。导入只处理启用且结果落库（`STORE`）的指标；标准“目标值”表按明细行处理，分层模板按指标列处理。无效指标列、节点或数据行会跳过并在结果中提示，其他有效内容继续导入，已有未涉及目标值不会被清空；先下载最新模板再编辑。
3. 点击“保存草稿”。草稿可反复修改，保存其他草稿不会改变正在使用的实时口径。
4. 选择已经保存目标值、且当前日期落在生效区间内的草稿，点击“设为实时目标”。系统会取消同一场景、同一目标周期下其他草稿的实时资格。该动作只切换实时展示，不发布考核版本。
5. 确认当期考核口径后，点击“发布为考核版本”。系统会复制当前草稿及其全部目标值，生成只读版本；源草稿保持 `DRAFT`，仍可继续调整并再次发布。
6. 在“版本记录”查看已发布版本、使用周期、发布时间和全部目标值。需要调整时使用“以此版本创建草稿”，在新草稿中修改后再按上述流程处理；不要尝试修改已发布版本。

发布空草稿会被拒绝。实时草稿则应先保存完整目标值再选择：空草稿虽然不能发布，却会让匹配它的实时页面没有可计算的目标。设置生效日在未来的草稿为实时目标也会取消原草稿的实时资格；在生效日期到来前，当前实时查询会返回空目标。因此，切换实时目标前必须同时检查目标值条数和生效日期。

#### 生效日期、历史口径与同日修订

`effective_from` 和 `effective_to` 表示目标实际使用的业务日期区间，两端均包含在内，不是“发布时间”。发布新考核版本时，系统会维护同一场景、同一周期的时间线：

- 新版本从较晚日期开始时，覆盖到该日期的旧考核版本会被截断至前一天并标记为 `RETIRED`。
- 已存在未来版本时，新发布版本的结束日期会自动落在下一个版本开始日前一天。
- 新旧版本使用同一生效日期时，旧 `ACTIVE` 版本会保留为 `RETIRED` 审计记录；新版本的 `version_no` 更高，查询时优先使用新版本。
- 查询候选版本时，系统按优先级、较晚的生效日期、较高的版本号和记录 ID 排序。优先级只应处理确有业务依据的覆盖关系；不要用同一日期、不同名称的多个重叠方案制造不透明口径。

例如，先发布“日常 / 月目标 / 2026-07-01 起”的 `v3`，之后因新增指标又从 `2026-07-01` 发布 `v4`：`v3` 仍在版本记录中可审计，但 7 月任一业务日期会使用 `v4` 计算完成率。若是 8 月的新口径，应把新草稿的生效日期设为 `2026-08-01`；发布后旧版本的有效期截至 `2026-07-31`，7 月历史仍使用旧版本，8 月开始使用新版本。

这里的“不回写历史”仅指 `metric_snapshot`、`metric_acc` 等指标事实数据不会被目标发布修改。历史完成率不是在采集时永久写入某个目标版本 ID，而是在查询时按历史**业务日期**解析考核版本。因此，同一生效日的修订是有意允许的例外，它会重新计算该日期范围的历史完成率；需要保留“当时曾按哪个版本出分”的不可变结算结果时，必须另行建立结算快照或在指标快照中固化考核版本 ID，当前实现不提供这层绑定。

#### 接口与模板

目标管理由 FastAPI 的 `/api/dashboard` 路由提供，前端不直接写数据库：

| 操作 | 接口 | 规则 |
| --- | --- | --- |
| 列出方案 | `GET /target-plans?status=DRAFT|ACTIVE|RETIRED` | 返回状态、版本、生效区间、实时标记和目标值条数。 |
| 新建草稿 | `POST /target-plans` | 只创建 `DRAFT` 元数据。 |
| 查询目标值 | `GET /target-values?plan_id=...` | 可按层级、指标和名称过滤。 |
| 保存目标值 | `PUT /target-plans/{id}/values` | 仅允许 `DRAFT`，按节点 × 指标新增或覆盖提交的目标值。 |
| 切换实时目标 | `POST /target-plans/{id}/use-for-realtime` | 仅允许 `DRAFT`，会取消同场景同周期其他草稿的实时标记。 |
| 发布考核版本 | `POST /target-plans/{id}/activate` | 仅允许非空 `DRAFT`，生成独立只读副本。 |
| 从版本创建草稿 | `POST /target-plans/{id}/clone` | 复制版本和目标值，新草稿默认不作为实时目标。 |
| 模板导入导出 | `GET /target-template`、`GET /target-plans/{id}/export`、`POST /target-template/import?plan_id=...` | 导入兼容标准“目标值”明细表；也兼容“区公司级/网格级/渠道经理级/渠道级/指标参考表”分 Sheet 格式，指标列按“指标参考表”中的名称映射编码。导入按行/列部分成功，响应返回 `skipped` 和 `skipped_count`；导出当前方案后的文件可直接修改并回传。 |

任何手工 SQL、导入脚本或新 API 都不得绕过这些状态约束去修改 `ACTIVE`/`RETIRED` 的 `MetricTargetValue`。需要批量调整时，先复制为草稿，再导入、切换实时或发布。

### 腾讯文档表格下载

下载配置中的报表可设为 `source: "tencent_sheet"`，并指定 `doc_url`（或
`file_id`）和至少一个 `sheets` 项。腾讯文档凭据保存在被 Git 忽略的
`config/runtime.local.json` 的 `module_overrides.tencent_docs`；可提交的
`config/modules/tencent_docs.json` 仅保留字段模板，不能填入真实凭据。

`sheets[].range` 支持显式 A1 范围和自动范围。使用空值、`auto`、`used` 或
`used_range` 时，系统优先按腾讯文档返回的实际数据行列边界下载；缺少该边界时
才回退到工作表容量。填写 `A1:Z1000` 等显式范围时会按原范围请求，不会被元数据
预先截断；若后续分块超出数据区，接口返回无效范围后将停止继续读取该 Sheet。

```json
{
  "source": "tencent_sheet",
  "name": "腾讯文档日报",
  "doc_url": "https://docs.qq.com/sheet/<文档标识>?tab=<Sheet ID>",
  "sheets": [
    {"sheet_id": "<Sheet ID>", "range": "auto", "output_sheet_name": "日报"}
  ]
}
```

常用私有分区为 `module_overrides.login_config`、
`module_overrides.wecom_sender` 和 `module_overrides.tencent_docs`；它们全部位于
`config/runtime.local.json`。

### 腾讯文档与智能表格

前端“数据抓取”分为“业务接口”“腾讯文档”和“智能表格”三个独立标签；后两个标签共用
腾讯文档 OpenAPI 凭据，但配置契约不同：

| 数据源 | `source` | 子表选择 | 读取方式 |
| --- | --- | --- | --- |
| 普通腾讯 Sheet | `tencent_sheet` | `sheet_id` 或 `sheet_name` | `range` 为 `auto` 或显式 A1 范围 |
| 腾讯智能表格 | `tencent_smartbook` | `sheet_id` 或 `sheet_name` | 导出全部字段与全部分页记录，禁止设置 `range` |

智能表格的 `sheet_id` 可直接使用链接中 `tab` 参数的值；同时填写 `sheet_id` 和
`sheet_name` 时以 ID 为准。`sheets[]` 的配置顺序就是导出工作簿中各输出 Sheet 的顺序，
`output_sheet_name` 可改写输出 Sheet 名。字段标题写入表头，选项、人员、链接等富值会转换为
可读文本；没有 `values` 的记录不会写入工作簿。

腾讯文档 OpenAPI 凭据仅存放在被 Git 忽略的
`config/runtime.local.json` 的 `module_overrides.tencent_docs`，必须包含 `client_id`、
`access_token` 与 `open_id`；前端不会读取或上传这些凭据。智能表格配置示例：

```json
{
  "credentials": {
    "client_id": "<Client-Id>",
    "access_token": "<Access-Token>",
    "open_id": "<Open-Id>"
  }
}
```

```json
{
  "source": "tencent_smartbook",
  "name": "智能表格日报",
  "doc_url": "https://docs.qq.com/smartsheet/Dexample?tab=sheet-1",
  "output_filename": "智能表格日报.xlsx",
  "sheets": [
    {
      "sheet_id": "sheet-1",
      "output_sheet_name": "汇总"
    }
  ]
}
```

可在不改动业务配置的情况下手动诊断一个子表；命令只输出导出结果的摘要，不会打印凭据：

```powershell
python scripts/dev/test_tencent_smartbook_export.py --doc-url "https://docs.qq.com/smartsheet/Dexample?tab=sheet-1" --sheet "id:sheet-1" --output "temp/smartbook.xlsx"
```

### 统一运行配置

将 `config/runtime.local.example.json` 复制为 `config/runtime.local.json`。该唯一私有文件依次维护运行目录与 Pool、Prefect、驾驶舱 MySQL 与锁覆盖、监控 Webhook 密钥、模块凭据和任务差异；它已被 Git 忽略，不能提交。

从旧版本升级时，先在目标机器预览迁移，再写入并删除已验证的旧配置文件：

```powershell
python scripts/migrate_local_json_to_runtime.py
python scripts/migrate_local_json_to_runtime.py --apply
python scripts/migrate_local_json_to_runtime.py --apply --remove-legacy
```

预览和冲突信息只显示配置路径，不显示凭据值。若出现冲突，保留两份文件并手工处理后重新从预览开始；开发机和生产机必须分别执行迁移。

启动脚本只读取 JSON 配置。
`prefect.postgres.url` 指定 Prefect 直接使用的 PostgreSQL 数据库；必须与当前机器所属环境一致，不能复用其他环境的库。
运行配置必须声明固定共享目录 `C:\AutoNotifyRuntime` 和三个 Process Work Pool：

| Work Pool | 并发上限 | 运行内容 |
| --- | ---: | --- |
| `windows-session-pool` | 1 | Session Keeper |
| `windows-dashboard-pool` | 4 | 驾驶舱采集、同步和维护 Flow |
| `windows-notify-pool` | 6 | 完整通报 Flow |

### 环境数据库拓扑

| 环境 | Prefect PostgreSQL | 驾驶舱 MySQL | 用途 |
| --- | --- | --- | --- |
| 云电脑 / 生产 | `prefect_prod` | `dashboard_prod` | 正式调度、生产 Worker 与驾驶舱服务。 |
| 本机开发 | `prefect_dev` | `dashboard_dev` | 本地开发、调试和验收。 |
| 保留源库 | `prefect_test` | `dashboard_v2` | 旧数据留存与迁移审计；不再作为新环境的运行库。 |

本机的 `config/runtime.local.json` 应配置为 `prefect_dev` 和 `dashboard_dev`；云电脑上的同名本地配置应配置为 `prefect_prod` 和 `dashboard_prod`。两份配置都属于被 Git 忽略的机密运行配置，严禁提交凭据。

Prefect 的生产库与开发库在初始迁移时使用同一份完整快照，保留 Deployment、Work Pool、运行记录和日志。两个环境开始运行后，调度和运行状态自然分叉；不得建立双向同步，也不得将开发库回灌生产库。

驾驶舱 MySQL 仅迁移组织树、指标定义、公式组件和目标方案等配置数据；采集运行、实时值、累计值和指标快照由各环境自行生成。旧库应保留，直到完成切换验收与回滚窗口。

在旧 Worker 预检通过并启动 Prefect Server 后，脚本会在启动任何新 Worker 前取消本项目全部已过期的排队 `SCHEDULED` / `PENDING` Run；随后启动并确认 Session Worker 在线，唯一一次提交新的 Session Keeper Run，最后启动 Dashboard 和 Notify Worker。提交不会等待 Session Keeper Flow 完成。预计开始时间仍在未来、缺少预计开始时间的排队 Run，以及不属于本项目受管 Deployment 的 Run，会原样保留且不受此清理影响。`RUNNING`、`CANCELLING` 或 `PAUSED` 的 Run 仍只会在 `scripts/run.ps1 -ForceRestart` 时取消。

运行时目录由 `config/runtime.local.json` 的 `runtime.root` 指定，默认是
`C:\AutoNotifyRuntime`：

```text
C:\AutoNotifyRuntime\
  session\
    stages\                 已验证的业务阶段会话快照
    stage_health.json        阶段会话健康状态
    browser-session.json
    browser-profile\
    locks\
      login.lock
      excel_com.lock
      dashboard_collection.lock
    login_debug\
  config\
    drafts\                  前端保存、尚未发布的通报配置草稿
    versions\<配置名>\        正式配置覆盖前的历史备份
  logs\
    web_runs.jsonl           Web/后端运行日志
  health\                    健康检查探针
  starter_templates\         新手模板生成的中间产物
  modules\<模块名>\output\   模块独立产物
  flow\<任务名>\
    output\
    backup\
    debug\
    tmp\
  temp\                      工具临时文件
  prefect\prefect_home\      Prefect Home；本机 Server 与 Worker 共用
  processes\                 受管 Server、Worker、后端和前端的进程登记 JSON
```

所有配置、API 路径和运行文件记录都使用相对于 `runtime.root` 的受控路径，例如
`session/...`、`config/...`、`modules/<模块名>/output/...` 或
`flow/<任务名>/tmp/...`。不得传递绝对路径、`..` 或历史 `runtime/...` 前缀；
这些值会被拒绝，而不会回退到仓库目录。

`session/stages/`、`browser-profile/` 和 `browser-session.json` 是敏感登录态，
不能提交、公开、复制或随意删除。`cookie_dump.json` 只可由 Session Broker 在
内部兼容步骤中临时创建，并在操作结束后删除；业务 Flow 和运维脚本不得直接读取它。
`processes/` 是 `scripts/stop.ps1` 安全识别本项目进程的依据；停止运行栈前
不得手工删除登记文件。`prefect/prefect_home/` 与这些共享状态不随代码升级、
分支或 Worktree 切换。旧仓库 `runtime/` 路径不再参与启动；如需迁移旧状态，
只能在停止运行栈后人工执行 `Invoke-RuntimeStateMigration`。

`setup_windows_env.ps1` 与 `run.ps1` 使用同一个机器级运行时互斥锁，禁止
setup/setup 或 setup/startup 并发修改共享目录。

项目固定 `Prefect 3.7.0`，并将 FastAPI 限制在 `0.115` 系列以避免与较新
Starlette 路由接口不兼容。安装或更新依赖时请使用 `requirements.lock`。

## 启动

启动必须由专用 Windows 用户手工执行。该用户需要保持登录和交互式桌面会话；主机重启或用户重新登录后，必须再次执行 `scripts/run.ps1`。Prefect Server 停止后也不会自动恢复，需要人工重新运行启动脚本。

### C 盘完整恢复操作手册

本节是将已验证的 `release/v1.1.0` 部署到 `C:\AutoNotify` 的完整连续流程。它适用于两类情况：将原桌面目录迁移到 C 盘，或在新 Windows 机器上恢复同一环境。执行过程中只使用 PowerShell，除特别说明外，所有命令均在 `C:\AutoNotify` 运行。

| 阶段 | 会修改的对象 | 完成标志 |
| --- | --- | --- |
| 停旧栈与克隆 | 受管进程、C 盘代码目录 | 旧进程停止，新目录位于 `release/v1.1.0` |
| 私有配置与依赖 | 本机被忽略配置、`node_modules`、运行目录 | `smoke_ok` |
| 数据库与分区 | 当前环境的驾驶舱 MySQL | Alembic 为 `head`，未来分区完整 |
| Prefect 重建 | 当前 Prefect API 的 Deployment | YAML 中 19 个 Deployment 均存在 |
| 统一启动与验收 | Server、三个 Worker、后端和三个前端页面 | `status.ps1` 和 HTTP 健康检查正常 |

这套流程不会把数据库数据、Flow Run 历史、Cookie、Edge Profile 或 `C:\AutoNotifyRuntime` 放入 Git。代码仓库必须位于 `C:\AutoNotify`，运行状态目录固定为 `C:\AutoNotifyRuntime`；不要把仓库克隆到运行状态目录中。

#### 0. 确认环境并停止旧运行栈

先决定本次操作属于开发环境还是生产环境。开发环境只能连接 `prefect_dev` 与 `dashboard_dev`；云电脑生产环境只能连接 `prefect_prod` 与 `dashboard_prod`。二者的 `runtime.local.json`、Cookie、数据库、监控密钥和 MySQL 锁名必须隔离，不能相互复制。

在旧项目目录先停止受管进程。不要手工删除 `C:\AutoNotifyRuntime\processes` 中的登记文件，也不要直接结束未知的 Python、Node 或浏览器进程：

```powershell
pwsh -File scripts/stop.ps1
```

若旧 Worker 仍持有任务，先在 Prefect UI 确认其不再执行关键业务后再停止。普通恢复不要使用 `-ForceRestart`；该选项会取消本项目正在运行的 Flow Run。

#### 1. 克隆已验证的发布分支

标准克隆会保留该分支的全部提交历史；不会丢失 Git 内容。若需要查看其他远端分支，可在克隆后执行 `git fetch origin` 和 `git branch -r`：

```powershell
git clone --branch release/v1.1.0 https://github.com/henuymy/auto_prefect.git C:\AutoNotify
Set-Location C:\AutoNotify
git branch --show-current
git log -1 --oneline
git status --short
```

预期当前分支为 `release/v1.1.0`，工作区没有受版本控制的改动。`config/runtime.local.json`、本地 Excel 模板、`node_modules`、`C:\AutoNotifyRuntime`、数据库和 Prefect 历史都不随克隆出现，这是正常的安全边界。

#### 2. 创建或安全迁移本机运行配置

全新环境从模板创建私有配置：

```powershell
Copy-Item config\runtime.local.example.json config\runtime.local.json
```

同一台机器仅更换代码目录时，可以从旧目录迁移该环境自己的私有文件；此操作只允许在同一开发环境内或同一生产环境内进行，绝不能把开发文件带到云电脑生产环境，反之亦然：

```powershell
Copy-Item '<旧项目根目录>\config\runtime.local.json' .\config\runtime.local.json
```

编辑 `config\runtime.local.json`，按固定顺序填写：`runtime`、`prefect`、`dashboard`、`monitor`、`module_overrides`、`task_overrides`。其中必须包含运行根目录、三个 Work Pool、Prefect PostgreSQL、驾驶舱 MySQL、环境独有的 `monitor.prefect_webhook_secret` 和本环境模块凭据。不要在命令行、截图、README 或 Git 中输出这些值。

加载配置后，只显示非敏感的目标库名以确认环境：

```powershell
. .\scripts\lib\runtime_config.ps1
Import-ProjectRuntimeConfig | Out-Null
Write-Host "Dashboard database: $env:DASHBOARD_MYSQL_DATABASE"
```

`collection_database_lock_name` 与 `partition_database_lock_name` 必须在开发、生产之间使用不同后缀，例如 `_dev`、`_prod`；同一环境中的所有机器必须使用同一把锁名。

#### 3. 安装锁定依赖并初始化运行目录

项目要求 Conda `base` 中的 Python 3.13 和 Node 20.20.1。先安装或切换 Node，再安装前端锁定依赖；然后运行 Python 初始化脚本。脚本会创建 `C:\AutoNotifyRuntime` 所需目录、检查 Edge/Excel，并进行 Python 基础自检：

```powershell
conda activate base
nvm install 20.20.1
nvm use 20.20.1
node --version
npm --prefix frontend ci
pwsh -File scripts/setup_windows_env.ps1
```

预期 `node --version` 为 `v20.20.1`，`npm ci` 按锁文件成功完成，初始化末尾输出 `smoke_ok`。若 `nvm use 20.20.1` 报版本未安装或版本目录缺少 `node.exe`，先修复或重新安装该 Node 版本，再执行 `npm ci`；不要通过提交 `frontend/node_modules` 绕过问题。若 Python 镜像返回 `HTTP 403`，可按“安装”章节临时使用官方 PyPI。

`setup_windows_env.ps1` 与 `run.ps1` 共享机器级互斥锁，禁止并发执行。初始化失败时，不能继续数据库迁移或启动 Worker。

#### 4. 迁移驾驶舱数据库并维护未来分区

确认目标数据库正确后，先读取当前 Alembic 版本，再执行幂等升级。生产库操作前必须已有可用备份；不要用开发库数据覆盖生产库：

```powershell
. .\scripts\lib\runtime_config.ps1
Import-ProjectRuntimeConfig | Out-Null
python -m alembic -c .\alembic_dashboard_v2.ini current
python -m alembic -c .\alembic_dashboard_v2.ini upgrade head
python -m alembic -c .\alembic_dashboard_v2.ini current
```

预期最后一条显示当前版本为 `head`。这条命令只处理项目自有的驾驶舱 MySQL 结构；Prefect PostgreSQL 结构由 Prefect Server 自己维护。

Alembic 到达 `head` 不代表驾驶舱可用。系统要求提前创建未来日期的 `metric_snapshot` 分区。首次部署、长期停机恢复，或后端 `/api/health` 返回 `503` 且响应中 `dashboard_schema.missing_snapshot_partitions` 非空时，执行一次独立分区维护：

```powershell
. .\scripts\lib\runtime_config.ps1
Import-ProjectRuntimeConfig | Out-Null
python flows/dashboard_partition_maintenance_flow.py
```

该 Flow 只创建缺失分区、清理过期数据并回收陈旧运行引用，不采集报表或发送通报。若出现 MySQL `1205 Lock wait timeout exceeded`，说明采集或其他事务正在占用锁；优先等待其结束，必要时先停止 Worker，再重新执行维护，不要终止未知数据库连接。

#### 5. 从 YAML 重建 Prefect Deployment

`prefect.yaml` 是当前 19 个 Deployment 的声明来源，包含 Session Keeper、5 个驾驶舱任务和 13 个通报任务。每次更换项目目录后都必须从新目录发布，保证 Flow 入口和相对 `config_path` 指向 `C:\AutoNotify`。

先只启动 Prefect Server，等待 API 返回 200：

```powershell
pwsh -File scripts/lib/prefect_start.ps1 -Mode server -Detached
Invoke-WebRequest -Uri http://127.0.0.1:4200/api/health -UseBasicParsing
```

常规目录迁移不需要删除已有 Deployment，直接重新发布即可。只有在确认当前 `PREFECT_API_URL` 不包含任何其他项目，并且需要彻底重建 Deployment 元数据时，才按下面的完整重建流程执行；`--all` 会删除该 API 的所有 Deployment，不能撤销：

```powershell
. .\scripts\lib\runtime_config.ps1
Import-ProjectRuntimeConfig | Out-Null
python -m prefect deployment ls
python -m prefect deployment delete --all --no-prompt
python -m prefect deployment ls
```

发布全部 YAML 声明并核对清单：

```powershell
. .\scripts\lib\runtime_config.ps1
Import-ProjectRuntimeConfig | Out-Null
python -X utf8 -m prefect deploy --all
python -m prefect deployment ls
```

预期远端有 19 个 Deployment，Pool 分别为 `windows-session-pool`、`windows-dashboard-pool`、`windows-notify-pool`。YAML 是 Cron、`Asia/Shanghai` 时区和 `schedules[].active` 状态的唯一来源；发布不会绕过暂停状态，也不会立即发送所有通报。

发布结束后关闭仅用于引导的 Server，让下一步的统一启动脚本重新接管受管进程：

```powershell
pwsh -File scripts/stop.ps1
```

#### 6. 统一启动完整运行栈

不要分别手工启动 Worker、后端或 Vite 页面。统一入口会校验 PostgreSQL 与 MySQL、启动 Prefect Server、创建或校正三个 Process Work Pool、清理过期队列、启动三个 Worker 和四个 Web 进程：

```powershell
pwsh -File scripts/run.ps1
pwsh -File scripts/status.ps1
```

`run.ps1` 会提交一次 `session-keeper-flow/session-keeper` 检查，但不会等待它结束。它不会自动执行全部通报；通报是否排队由 YAML 中各 Deployment 的 Cron 和 `active` 状态决定。若普通启动发现已登记的旧 Worker，先执行 `scripts/stop.ps1`，不要为绕过这一保护直接使用 `-ForceRestart`。

#### 7. 启动后验收和故障分流

`status.ps1` 应显示 Prefect Server、Session/Dashboard/Notify Worker、后端、配置中心、驾驶舱和监控中心均为运行状态；三个 Pool 应各有一个在线 Worker。再逐个检查 HTTP 入口：

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:4200/api/health -UseBasicParsing
Invoke-WebRequest -Uri http://127.0.0.1:8000/api/health -UseBasicParsing
Invoke-WebRequest -Uri http://127.0.0.1:5173/ -UseBasicParsing
Invoke-WebRequest -Uri http://127.0.0.1:5174/ -UseBasicParsing
Invoke-WebRequest -Uri http://127.0.0.1:5175/ -UseBasicParsing
python -m prefect deployment ls
python -m prefect flow-run ls --flow-name session-keeper-flow --limit 1
```

验收标准如下：

- Prefect API、后端健康检查、5173 配置中心、5174 驾驶舱和 5175 运行监控中心均返回 `200`。
- `/api/health` 的顶层 `ok` 为 `true`，`dashboard_schema` 版本为 `head`，且没有缺失分区。
- 首次 Session Keeper Flow 最终为 `Completed`；登录锁在运行结束后释放。
- Prefect 中有 19 个 Deployment，名称、Pool、Cron、时区和暂停状态与 `prefect.yaml` 一致。
- 运行监控中心可打开，但其“上游事件”和“最近对账”只有在符合过滤条件的通报运行后才会更新；页面 WebSocket 已连接不等于 Prefect Webhook 已成功投递。

常见故障按以下顺序处理：Node 版本无法切换时先修复 NVM；`/api/health` 为 503 且仅缺分区时运行分区维护；Worker 提示旧路径时在 C 盘目录重新执行 `prefect deploy --all`；旧 Worker 或活动 Run 阻止启动时先在 Prefect UI 核对真实状态，再停止或按批准范围取消。任何情况下都不要提交 `runtime.local.json`、Cookie、数据库密码、Webhook、Token 或证书。

标准操作顺序：

```powershell
pwsh -File scripts/setup_windows_env.ps1
pwsh -File scripts/run.ps1
pwsh -File scripts/status.ps1
pwsh -File scripts/stop.ps1
```

`scripts/run.ps1` 在旧 Worker 预检通过并启动 Prefect Server 后，会在启动任何新 Worker 前取消本项目全部已过期的排队 `SCHEDULED` / `PENDING` Run；随后启动并确认 Session Worker，唯一一次提交新的 Session Keeper Run，最后启动 Dashboard 和 Notify Worker。该提交不等待 Flow 完成。预计开始时间仍在未来、缺少预计开始时间的排队 Run，以及不属于本项目受管 Deployment 的 Run，会原样保留且不受此清理影响。脚本不会发布、删除或同步仓库中的 Deployment 定义；为避免清理期间产生新 Run，它会临时暂停并恢复原本未暂停的受管 Deployment，并将 Notify Deployment 的 Work Pool 校正为运行配置指定的 Pool。通报和驾驶舱采集仍由既有 Cron 调度执行，不会自动触发全部通报。`scripts/status.ps1` 从 Prefect API 读取配置中的三个 Pool 名称和实际并发上限，因此验收期 Notify 上限为 2 时会显示 2；它也逐条标识排队超过 10 分钟的自动调度 Run。锁文件当前不记录等待者，状态只显示所有者和持有时长，并明确显示 `waiters=unavailable`。`scripts/stop.ps1` 仅停止 `C:\AutoNotifyRuntime\processes` 中登记且进程身份匹配的本项目进程。

为避免旧 Worker 在启动清理前领取排队任务，普通启动会先检查已登记的 Session、Dashboard 和 Notify Worker；任一 Worker 仍在运行时，脚本会在启动 Prefect Server 前拒绝继续。应先执行 `scripts/stop.ps1` 确认旧进程停止，再正常运行 `scripts/run.ps1`；不要仅为绕过该检查使用 `-ForceRestart`，因为该选项还会取消运行中的本项目 Run。

升级时若旧 Notify Pool 仍有预计开始时间未到或缺失的保留 Run，启动脚本会列出 Deployment、Run ID、状态和旧 Pool 并拒绝启动。Prefect 3.7 不支持安全改派单个已排队 Run，且启动旧 Pool Worker 可能执行同 Pool 的无关工作；运维人员应先在受控条件下用旧 Pool Worker 排空或取消列出的 Run。此时 Prefect Server 已注册，处理后先执行 `scripts/stop.ps1` 再运行 `scripts/run.ps1`。`scripts/run.ps1 -ForceRestart` 只取消运行中的 Run，不能绕过旧队列的失败关闭。不得删除历史 Run 或把它们静默遗留在无 Worker 的 Pool。

一键启动本地 Prefect、API 和前端：

```powershell
pwsh -File scripts/run.ps1
```

查看或停止服务：

```powershell
pwsh -File scripts/status.ps1
pwsh -File scripts/stop.ps1
```

仅在本地开发或诊断时，可直接调用内部 Web 启动脚本：

```powershell
pwsh -File scripts/lib/start_web.ps1 -Mode both
```

前端启动后提供三个独立入口：

- 配置中心：`http://127.0.0.1:5173/`
- 数据驾驶舱：`http://127.0.0.1:5174/`
- 运行监控中心：`http://127.0.0.1:5175/`

云电脑使用 FRP 部署时，当前 `frp/frpc.toml` 将配置中心的本地 `5173` 映射为远端 `15173`，将数据驾驶舱的本地 `5174` 映射为远端 `15174`。对应的公网访问地址为 `http://服务器地址:15173/` 与 `http://服务器地址:15174/`。不要把驾驶舱端口写成 `15176`，除非同时修改 FRP 配置并重启 `frpc`。

驾驶舱页面中的历史挡位请求使用同源 `/api/dashboard/history/...` 地址，由 `5174` 的 Vite 代理转发到本机后端 `8000`，不需要额外映射后端端口。外部访问历史失败时，先确认访问的是 `15174`，再检查 `http://服务器地址:15174/api/dashboard/history/options`；本地 `5174` 正常而 `15174` 失败，优先检查 `frpc` 进程、FRP 服务端端口和云服务器安全组。

日常运行仍应使用 `scripts/run.ps1`；`scripts/lib/` 下的脚本是实现模块，不作为稳定的运维入口。

默认地址：

- 前端：`http://127.0.0.1:5173`
- 运行监控中心：`http://127.0.0.1:5175`
- 后端健康检查：`http://127.0.0.1:8000/api/health`
- Prefect UI：`http://127.0.0.1:4200`

## 运行监控中心

运行监控中心是面向通报任务的业务运行页，不替代 Prefect UI。它只展示真实通报 Deployment 的运行记录和待执行计划：Session Keeper、驾驶舱采集以及网页后台操作不会进入时间线、运行记录或待执行汇总。

运行 `scripts/run.ps1` 会将监控页作为受托管服务启动在 `http://127.0.0.1:5175/`。`http://127.0.0.1:5175/monitor.html` 仍可用于兼容访问；配置中心入口 `http://127.0.0.1:5173/monitor.html` 也保持可用。5175 不新增 FRP 映射。

- 运行记录和时间线保留最近 30 天，且只显示成功、运行中和失败；历史 `Scheduled` 记录不会混入这两个区域。
- 待执行是独立的全局通报 `Scheduled` 队列，展示真实的 `targetId`、下次计划时间和 `nextStep`（通常为“尚未开始”），不受运行记录筛选条件影响。
- 运行记录支持按对象、触发方式、状态和日期筛选，并支持每页 10、20、50 条；时间线按日期分组，可逐日收起或展开并分批加载。
- 点击记录会按需向 Prefect 官方 API 查询 Task Run 与脱敏日志。Prefect 暂不可用时，抽屉保留已同步的失败摘要并提示详情暂不可用。
- “页面实时”仅表示浏览器 WebSocket 状态；“上游事件”和“最近对账”分别反映 Prefect Automation Webhook 的最后接收时间与 REST 对账的最后成功时间，不能互相替代。
- 后台对账与 Webhook 均只投影 `auto-notify-flow` 中名称以 `notify-` 开头的通报 Deployment；驾驶舱采集等非通报 Flow Run 不写入 `monitor_runs`，也不会影响监控中心的对账状态。
- 进入监控投影的错误文本会先脱敏并限长：业务摘要最多 500 字符，技术摘要、步骤消息和日志详情最多 8,000 字符；完整原始异常仍以 Prefect 为准，避免超长异常导致 MySQL 投影写入失败。

实时主链路为 Prefect Automation Webhook -> FastAPI -> MySQL -> WebSocket。MySQL 是页面持久化投影，浏览器首次连接和断线重连均先读取 REST 快照；后台每 5 分钟通过 Prefect 官方 REST API 补漏和对账。当前为单 FastAPI 进程，WebSocket 广播仅覆盖该进程；扩展为多进程或多实例时才需要 Redis Pub/Sub 或 Streams。详细接口、密钥和验收方法见 [Prefect 通报监控 Webhook 运维手册](docs/operations/prefect-monitor-webhook.md)，完整架构见 [运行监控中心设计](docs/run-monitoring-center-design.md)。

### 云电脑实时事件接入

每个云电脑必须独立完成 Prefect 侧配置，不能复制其他机器的 `runtime.local.json`、Webhook Block 或密钥。后端从当前机器的 `monitor.prefect_webhook_secret` 读取认证值；Prefect 的 Webhook Block 也必须使用同一个当前机器的值，但不得在任何文档、截图、日志或提交中记录该值。

在 Prefect UI 中创建或复用名称为 `monitor-realtime-events` 的 Webhook Block：使用 `POST`、允许私有地址、启用证书校验，并包含 JSON 内容类型。回调目标必须是 Prefect Server 实际可访问的 FastAPI `8000` 接收端；仅当 Prefect Server 和 FastAPI 位于同一台云电脑时，才可使用本机回环地址。页面端口 `5175` 不能接收事件。

创建且只保留一条用途相同、已启用的事件型 Automation。它应仅匹配 `auto-notify-flow` 中名称以 `notify-` 开头的 Deployment 的 Flow Run 状态变化，并监听 Scheduled、Running、Completed、Failed、Crashed、Cancelled；动作为调用 `monitor-realtime-events` Block。不要让 Session Keeper、`dashboard-collection` 或其他非通报 Deployment 命中该规则。

部署后，手工运行一个 `notify-` Deployment，至少验证 Scheduled、Running、Completed 和一次 Failed 或 Crashed。每次匹配事件的 Automation 调用应返回 `202`，`/api/live` 的 `monitorEvents.lastAcceptedAt` 应更新，页面应出现相应记录；Scheduled 进入 Running 后必须从待执行队列移除。`monitorEvents.lastReconciledAt` 由五分钟 REST 对账更新，两条时间分别反映实时接收和补漏对账，任一项不能替代另一项。

若 `lastErrorCategory` 为 `RECONCILIATION_FAILED`，先检查 FastAPI 生命周期、Prefect API 和 MySQL 是否处于可用状态。Prefect Server 重启窗口可能造成一次瞬态失败；恢复服务后等待下一轮对账，并以 `lastReconciledAt` 前进且 `lastErrorCategory` 恢复为 `null` 为准。不要将浏览器 WebSocket 在线、`5175` 页面可访问或曾经接收到事件误判为对账成功。

## Session Keeper

Session Keeper 仅支持 Windows 部署，依赖持续存活的 Microsoft Edge 用户会话；日常探活不应关闭该浏览器。Prefect Deployment 名称为 `session-keeper-flow/session-keeper`，固定在 `Asia/Shanghai` 时区每 10 分钟运行。

- Session Keeper 与业务 Flow 都通过 `StageSessionBroker` 获取会话。Keeper 在同一个 Edge Profile 中预热 `report_analysis`、`smart_ops` 与 `city_ops` 阶段，业务 Flow 复用已验证的阶段数据；下载期间明确认证失效时仍可在全局登录锁内刷新一次，并仅重试失败下载一次。
- 每次 Session Keeper 成功完成预热后，Flow 日志会按阶段记录共享会话健康确认，便于在 Prefect UI 中核验本轮健康状态。
- `autologin.json` 中每个 stage 配置一个主探活和可为空的 `fallback_probes` 数组；主探活失败后依次尝试备用接口，任一成功即判定该 stage 健康。需要更严格的鉴权校验时可改用 `probes` 数组，所有启用探活都成功才判定该 stage 健康。探活必须动态读取当前会话的 Cookie 或 Storage，不得提交固定认证材料。
- 认证明确失效时只在全局登录锁内执行一次完整刷新，并仅重试失败的业务步骤一次；基础设施探活失败不触发登录。
- `city_ops` 探活使用连接超时 `2` 秒、读取超时 `5` 秒。仅 `requests` 网络异常会在等待 `0.5` 秒后重试一次，总共最多两次；已获得的 HTTP 响应不重试，`302`、`401`、`403` 仍按认证失效处理。两次网络异常后仅记录安全的异常类别，例如 `ConnectTimeout` 或 `ReadTimeout`。
- 自动登录使用专用目录 `C:\AutoNotifyRuntime\session\browser-profile`，与运维人员日常使用的 Edge Profile 隔离。成功登录后可以保留该专用无头 Edge；保留的是浏览器会话，不是登录锁。任何需要完整登录的 Flow 仍必须先取得 `C:\AutoNotifyRuntime\session\locks\login.lock`。
- 需要重新登录时，锁的所有者先定向查询命令行包含该专用 Profile 路径的 `msedge.exe` / `msedgedriver.exe`，再关闭它们。历史 PID 不能单独作为结束进程的依据，避免 Windows PID 复用时误伤无关进程；`taskkill` 只有退出码为 `0` 才视为关闭成功。
- 关闭后的等待是条件等待：每 `0.5` 秒重新确认专用 Profile 关联进程是否已退出，确认后立即启动新的 WebDriver。`browser_close_wait_seconds`（默认 `10` 秒）只是本次清理的总上限，并非固定睡眠时间。新的 WebDriver 若仅因 `user data directory is already in use`、`DevToolsActivePort` 等 Profile 释放窗口错误而失败，也会在同一上限内按相同轮询间隔重试；成功后立即继续登录。
- 清理查询不可用、进程持续存在或关闭失败时，系统保留 `session/browser-session.json`，记录安全分类 `browser_cleanup_failed`，并且不启动新的 Edge 抢占同一 Profile、不删除 Profile、不更新 Cookie。该分类不进入第二次完整登录；它已经完成了本轮动态等待。Session Keeper 的其他完整登录失败最多再尝试一次，两个尝试之间没有固定 `60` 秒睡眠；第二次开始前仍会重新执行上述 Profile 条件清理，进程已退出则立即继续。
- `Session Manager` 是受全局锁保护的唯一浏览器清理所有者。它确认清理完成后才启动登录子进程；子进程通过内部环境标记跳过重复关闭。直接手工运行登录脚本时，脚本仍会先执行同样的定向清理。
- 若普通启动发现旧 Session、Dashboard 或 Notify Worker 仍在运行，先执行 `pwsh -File scripts/stop.ps1` 并确认 Worker 已停止，再执行 `pwsh -File scripts/run.ps1`。`stop.ps1` 处理受管 Worker；专用 Edge 的生命周期由 Session Manager 在下一次完整登录前处理。禁止使用 `taskkill /IM msedge.exe` 等全局命令，以免关闭日常 Edge 窗口。
- 若 Prefect Worker 报出 `browser_cleanup_failed`，先检查其运行账户能否查询本用户进程：`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Select-Object -First 1 ProcessId"`。查询被拒绝时，系统会安全地停止本轮登录而不是假定 Profile 空闲；应修复 Worker 账户的 Windows/WMI 查询权限后再重试。登录子进程失败时，日志可额外记录受控的 `阶段`、Python `异常` 类名和白名单 `原因` 码，用于区分驱动启动、门户登录和会话捕获边界；禁止记录命令行原文、账号、密码、Cookie、Token、Storage、Authorization、完整 URL、查询参数或 Selenium 原始异常文本。
- Session Keeper 不发送会话失败或恢复通知。development 环境仅在最终业务 Run 失败时，按工作负载、业务标识和 Flow Run 去重后发送一次企业微信告警。
- 完整登录仅持久化调用方声明的业务阶段会话；调用方只消费 Broker 返回的内存 `stage_data`，不得重新读取 Cookie 文件。
- 请求状态码、探活原因与自动恢复边界见 [请求故障分类与处理](docs/request-failure-handling.md)。
- 会话告警去重状态保存在 `session/session-alerts/incident_state.json`；开发环境业务 Run 告警状态保存在 `session/business-alerts/incident_state.json`。两者都会映射到共享运行根目录，而不会写入代码仓库。在 Prefect UI 中打开上述 Deployment 查看最新运行，或执行 `python -m prefect flow-run ls --flow-name session-keeper-flow --limit 1`。
- 支持日志不得复制账号密码、Cookie、Token、Webhook 值或其他认证材料。

发布统一 Keeper 后，必须在 Prefect UI 或命令行删除旧的远端部署及其调度；仅从 `prefect.yaml` 删除不会清理服务端已有对象：

```powershell
python -m prefect deployment delete "session-keeper-flow/session-keeper-report"
python -m prefect deployment delete "session-keeper-flow/session-keeper-city"
```

先确认新的 `session-keeper-flow/session-keeper` 已发布并可运行，再执行上述一次性清理。`scripts/run.ps1` 不发布或删除 Deployment。

## 积压与故障恢复

- 每次启动都会取消本项目全部已过期的排队 `SCHEDULED` / `PENDING` Run，不区分自动或手工触发；预计开始时间在未来或缺失的排队 Run 会保留。取消后不补跑 Session Keeper、Dashboard、Notify 或维护任务。
- `RUNNING`、`CANCELLING` 或 `PAUSED` 的本项目 Run 不会在普通启动时自动清理，并会阻止启动替代 Worker；只有 `scripts/run.ps1 -ForceRestart` 才会取消这些运行中的 Run。
- 普通启动发现已登记的 Session、Dashboard 或 Notify Worker 仍在运行时会失败关闭；先执行 `scripts/stop.ps1`，再正常启动，以保证过期 Run 清理先于任何 Worker 领取任务。
- Worker 崩溃后由各自的监督进程等待 30 秒再重启；Prefect Server 停止后不自动重启，使用 `scripts/run.ps1` 手工恢复。
- `windows-notify-pool` 允许通报并行，但所有 Excel COM 阶段必须通过 `C:\AutoNotifyRuntime\session\locks\excel_com.lock` 串行；所有会话刷新通过 `C:\AutoNotifyRuntime\session\locks\login.lock` 保证只有一个登录所有者。

## 主要入口

```text
flows/notify_single_flow.py                  单个通报任务
flows/dashboard_metric_flow.py               驾驶舱指标采集
flows/dashboard_partition_maintenance_flow.py 驾驶舱分区维护
backend/app.py                                FastAPI 应用
frontend/src/main.tsx                         配置中心前端
frontend/src/dashboard-main.tsx               数据驾驶舱前端
```

需要用仓库配置新建或更新部署时，手工发布 Prefect 部署：

```text
python -X utf8 -m prefect deploy --all
```

执行前必须确保当前环境已加载 `config/runtime.local.json` 对应的 Prefect API 配置。UTF-8 模式可避免 Windows 默认 GBK 编码读取包含中文的 `prefect.yaml` 时发生解码失败。Deployment 会保存发布时的代码加载路径；迁移电脑或项目目录后必须在新目录重新发布，否则 Worker 可能继续访问旧路径并报 `WinError 3`。该命令会创建或更新 `prefect.yaml` 中声明的全部 19 个 Deployment；新增通报或调整 Cron 后，也必须先同步更新 YAML。

### 清空并重建 Deployment

仅在需要重建当前 Prefect 环境、且确认该 API 不包含其他项目 Deployment 的维护窗口内使用。先停止本项目 Worker、确认没有需要保留的运行中 Flow Run，并检查将受影响的对象：

```powershell
. .\scripts\lib\runtime_config.ps1
Import-ProjectRuntimeConfig | Out-Null
python -m prefect deployment ls
```

确认后删除当前 API 的全部 Deployment：

```powershell
python -m prefect deployment delete --all --no-prompt
python -m prefect deployment ls
```

`--all` 不会限定为本项目，会删除当前 `PREFECT_API_URL` 下的所有 Deployment；它不删除 Flow Run 历史、Work Pool、Automation、数据库或本地运行配置。确认 Prefect Server 已运行且 `prefect.yaml` 包含所需的完整声明后，再恢复发布：

```powershell
python -X utf8 -m prefect deploy --all
```

发布后仍需执行 `pwsh -File scripts/run.ps1` 启动 Worker 和管理端；所有 Cron 是否启用以 YAML 中各 `schedules[].active` 为准。

## 数据库迁移

驾驶舱仅使用 V2 Alembic 配置：

```powershell
. .\scripts\lib\runtime_config.ps1
Import-ProjectRuntimeConfig | Out-Null
python -m alembic -c .\alembic_dashboard_v2.ini current
python -m alembic -c .\alembic_dashboard_v2.ini upgrade head
python -m alembic -c .\alembic_dashboard_v2.ini current
```

执行迁移前必须确认连接的是目标环境，并先完成数据库备份。使用 `python -m alembic` 可确保调用当前 Conda/Python 环境中的 Alembic，而不是 PATH 上的其他版本。最后一条应显示当前修订为 `head`。

`20260729_0005_target_plan_draft_publish` 是目标双轨管理迁移：它新增 `target_plan.is_realtime`，允许草稿与已发布副本共享版本号，并增加实时草稿查询索引。它不会修改已有目标值、指标快照或累计指标。升级既有环境后，已有发布方案仍可供历史查询；管理员应另行创建或选择当前使用的 `DRAFT` 并点击“设为实时目标”。

若日志显示：

```text
Running upgrade 20260719_0004 -> 20260729_0005, Allow editable target drafts and published versions to share a version number.
```

表示本次结构迁移已经执行，不是报错。`Context impl MySQLImpl` 和 `Will assume non-transactional DDL` 分别说明目标是 MySQL，且 MySQL DDL 不按事务回滚；执行前的数据库备份仍然是必需的。

驾驶舱 V2 分区维护直接运行前，必须先确保当前进程环境已加载 `config/runtime.local.json` 中的 `DASHBOARD_MYSQL_*` 配置；否则会报缺少 MySQL 环境变量。配置就绪后执行：

```text
python flows/dashboard_partition_maintenance_flow.py
```

若维护任务报 MySQL `1205 Lock wait timeout exceeded`，表示驾驶舱采集或其他事务正在锁定 `collection_run`。优先等待采集结束后重试；反复出现时，在确认没有必须保留的业务 Run 后停止运行栈，再运行维护任务并重新启动。不要把调大锁等待时间或直接终止未知数据库连接作为首选处理方式。

驾驶舱采集在读取当前组织结构时，若 MySQL 返回 `2006` 或 `2013`，会废弃当前连接池、等待 `0.5` 秒，并仅用新连接重试一次。日志中的 `MYSQL_CONNECTION_LOST` 或 `MYSQL_READ_TIMEOUT` 会带 `outcome=RETRY`、`RECOVERED` 或 `FAILED`，以及错误码、重试次数和耗时；日志不记录原始异常、SQL、连接地址或凭据。`RECOVERED` 表示本轮已自动恢复，无需人工补跑；`FAILED` 表示两次只读结构加载均失败，应先检查同一时段的 MySQL/代理日志、服务端负载和网络或 TLS 链路，再决定是否重新触发采集。该重试不适用于写事务。

实时指标写事务对 MySQL `2006` 或 `2013` 有单独的一次性恢复边界：原事务退出后，先确认采集命名锁仍由当前任务持有，再废弃写入连接池，并用新连接查询同一 `batch_no` 的 `collection_run`。若状态已经是 `SUCCESS`，表示 snapshot 插入、`metric_current` upsert、Run 完成状态和统计值已在同一事务中提交，只是应用未收到提交回执；系统直接返回已持久化的统计值，绝不重放 snapshot。若未观察到 `SUCCESS`，才等待 `0.5` 秒后完整重试一次；第二次 `2006`/`2013` 直接失败，不会无限重试。正常实时写入顺序为：规划 snapshot -> 插入 snapshot -> upsert 全部 `metric_current` 值 -> 标记 Run 为 `SUCCESS` -> 提交。当前值规划读取仅投影 `node_id`、`indicator_id`、`metric_value`、`stat_date`，不再对整批 `metric_current` 使用 `FOR UPDATE`；该优化依赖所有修改当前指标值的写入方均持有同一环境的 collection MySQL 命名锁。

Prefect PostgreSQL 的结构由 Prefect Server 迁移命令维护。迁移或全量恢复前，先停止或隔离会写入源库的 Server 与 Worker；恢复目标库后至少核验数据库迁移版本、各表行数、Deployment、Work Pool 与外键完整性。严禁用开发环境的状态覆盖生产库。

## 测试与质量检查

```powershell
python -m pytest -q
python -m ruff check .
cd frontend
npm run typecheck
npm run build
```

带 `mysql_integration` 标记的测试需要显式配置可丢弃的测试数据库；常规测试不会自动连接生产数据库。

## 运维约定

- 正式调度使用 PostgreSQL 作为 Prefect 元数据库；SQLite 仅用于本地调试。
- 真实试跑会发送企业微信消息，执行前检查接收范围和 Webhook。
- 正式模板仅在通知发送成功后提交；不要绕过提交门禁。
- 修改数据库结构必须新增 Alembic 迁移，不直接改生产表。
- Cookie、Edge Profile、共享锁、Prefect Home、日志和临时运行数据统一写入 `C:\AutoNotifyRuntime`；仓库内旧 Cookie/Profile 只作为首次安全迁移的来源，目标已存在时绝不覆盖。
- 前端构建产物和依赖目录可随时重新生成，不纳入版本控制。

## 故障定位

1. 运行 `scripts/status.ps1` 检查进程和端口。
2. 请求 `/api/live` 判断 API 进程是否存活，再请求 `/api/health` 检查依赖。
3. 查看 `C:\AutoNotifyRuntime\logs` 和 Prefect Flow Run 日志。
4. 登录失败时检查共享会话状态、Cookie 有效期和被忽略的本地登录配置，不输出认证材料。
5. 驾驶舱异常时检查 MySQL 连接、Alembic 版本和最近一次采集运行状态。若写事务日志记录 `outcome=RECOVERED_COMMITTED`，表示提交结果已恢复，不需要人工补跑；若 `2006`/`2013` 最终使 Run 失败，按“数据库迁移”章节核查 MySQL、代理与网络链路后再决定是否重跑。只读结构加载的 `outcome=RECOVERED` 同样不需要人工补跑。
6. 配置中心或数据驾驶舱无法打开时，检查 `frontend/node_modules` 是否存在，并确认 `npm --prefix frontend ci` 成功；配置中心和驾驶舱端口分别为 `5173`、`5174`。
7. `/api/health` 返回 `503` 时读取响应 JSON 的失败项；MySQL 可连接但 `dashboard_schema` 未就绪时，先运行驾驶舱 V2 分区维护。
8. 实时页面没有目标或完成率为空时，先在“目标值设置”确认当前场景与目标周期存在 `is_realtime=true` 的非空草稿，且其生效区间覆盖最近一次实时 Run 的 `stat_date`；再确认目标节点和指标仍处于启用状态。不要通过修改已发布版本补救实时页面。
9. 历史完成率与预期不一致时，先核对所选历史 Run 的业务日期，再在“版本记录”检查该日期命中的考核版本、生效区间、优先级和版本号。若同一生效日发布过修订版，历史完成率会按最新修订版重算；需要还原旧版口径时查看该版本记录，而不是假设历史页面仍按旧版计算。

## 安全边界

仓库中的示例配置不得包含生产密钥。提交前检查 `git status`，确保 `.local.*`、`runtime/`、`templates/`、Cookie、证书和数据库凭据没有进入暂存区。
