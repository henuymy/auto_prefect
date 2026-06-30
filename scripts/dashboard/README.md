# 数据驾驶舱脚本

本目录集中存放数据驾驶舱的维护和开发脚本。

## MySQL 环境

首次配置：

```powershell
Copy-Item scripts\dashboard\mysql_env.example.ps1 scripts\dashboard\mysql_env.local.ps1
```

加载环境变量：

```powershell
. scripts\dashboard\mysql_env.ps1
```

## 区域层级

V2 使用统一的 `hierarchy_node` 树。先执行独立迁移，再导入经过审批的
CITY/BRANCH/GRID 清单；CHANNEL_MANAGER 和 CHANNEL 由首次采集及结构漂移同步生成：

```powershell
. scripts\dashboard\mysql_env.ps1
alembic -c alembic_dashboard_v2.ini upgrade head
python scripts\dashboard\initialize_v2_hierarchy.py config\dashboard\v2_hierarchy.csv
```

示例格式见 `config/dashboard/v2_hierarchy.example.csv`。脚本会同时校验实际库名和
Alembic revision，不会连接错误数据库后继续写入。

从地市平台 `A / 郑州市` 开始导出组织层级：

```powershell
python scripts\dashboard\export_areas.py
```

将区域层级导入独立驾驶舱 MySQL：

```powershell
. scripts\dashboard\mysql_env.ps1
python scripts\dashboard\import_areas.py
```

生成请求目标：

```powershell
. scripts\dashboard\mysql_env.ps1
python scripts\dashboard\import_request_targets.py
```

## 指标候选

扫描现有报表配置并生成指标候选 Excel：

```powershell
python scripts\dashboard\export_indicator_candidates.py
```

## 单指标采集样本

完整采集 `sgs_ajvwdz` 并分析原始数值，不写入 MySQL：

```powershell
python scripts\dashboard\export_metric_sample.py
```

测试只读采集并发：

```powershell
. scripts\dashboard\mysql_env.ps1
python scripts\dashboard\benchmark_collection_concurrency.py
```

当前压测结论（2026-06-11，700 个目标）：

- 24 并发吞吐最高，作为生产默认值。
- 32 并发请求全部成功，但吞吐未继续提升，作为配置硬上限。
- 边界探测到 64 并发仍全部成功，但吞吐持续下降、P95 延迟明显上升，不宜用于生产。
- 实际采集由 `services/dashboard_collection_service.py` 统一限制并发并执行整批失败策略。

## 完整采集批次

手动执行会话探活、700 目标采集、4128 区域校验和事务写入：

```powershell
. scripts\dashboard\mysql_env.ps1
python scripts\dashboard\run_collection.py
```

V2 影子验证不需要提前改正式配置，可只覆盖本次命令：

```powershell
python scripts\dashboard\run_collection.py --schema-version 2 --mode REALTIME
python scripts\dashboard\run_collection.py --schema-version 2 --mode DAY_ACC
python scripts\dashboard\run_collection.py --schema-version 2 --mode MONTH
```

目标方案使用 JSON 导入为 DRAFT，人工核对后再激活：

```powershell
python scripts\dashboard\import_v2_target_plan.py 目标方案.json
python scripts\dashboard\import_v2_target_plan.py --activate-plan-id 目标方案ID
```

四类方案分别是 `NORMAL/DAY`、`NORMAL/MONTH`、`PK/DAY`、`PK/MONTH`。
格式见 `config/dashboard/v2_target_plan.example.json`。

整批共用一把采集锁。任一请求、区域校验或指标值转换失败时，
不会更新 `metric_snapshot` 和 `metric_current`。

批次会在请求前记录当天 `queryDate`。发现新增渠道或渠道经理请求异常时，
只同步受影响网格：先更新 `area`，再更新 `request_target`，然后整批重采一次。
同一批次最多执行一次局部结构同步，重采仍失败则整批失败，避免循环请求。

## 执行流程图

重新生成驾驶舱执行流程图片：

```powershell
pwsh -File scripts\dashboard\render_flow.ps1
```
