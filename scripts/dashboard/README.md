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
