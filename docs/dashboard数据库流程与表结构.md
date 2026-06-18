# 数据驾驶舱流程与表结构

## 1. 总体流程

### 读取链路
- 前端 `/api/dashboard/*` 进入 `backend/routers/dashboard.py`
- 路由里统一通过 `create_dashboard_engine()` 连接独立 MySQL
- 具体查询都落到 `services/dashboard_query_service.py`
- 配置和采集入口通过 `services/dashboard_batch_runner.py`、`services/dashboard_collection_orchestrator.py`、`flows/dashboard_metric_flow.py` 串起来

### 采集链路
- `POST /api/dashboard/collect` 提交 Prefect 流程
- `dashboard_metric_flow` 进入 `run_dashboard_metric_task`
- 批次生命周期由 `dashboard_batch_runner.py` 和 `dashboard_collection_orchestrator.py` 管理
- 采集结果写入独立 dashboard MySQL

### 连接方式
- 数据库配置由 `scripts/dashboard/mysql_env.local.ps1` 提供
- 当前本地连接目标：`60.205.108.31:3306/dashboard`
- 用户：`dashboard_app`
- 密码不写入文档

## 2. 关键表

| 表名 | 作用 |
|---|---|
| `collection_run` | 采集批次记录 |
| `area` | 区域树与启用状态 |
| `indicator` | 指标定义 |
| `request_target` | 采集目标 |
| `channel_manager_area` | 渠道经理与渠道关系 |
| `metric_current` | 实时当前值 |
| `metric_snapshot` | 历史快照值 |
| `metric_acc` | 日累计 / 月累计值 |
| `metric_target` | 目标值，用于完成进度 |
| `alembic_version` | 迁移版本 |

## 3. 表结构摘要

### `collection_run`
- 主键：`id`
- 唯一：`batch_no`、`prefect_flow_run_id`
- 主要字段：`run_type`、`trigger_type`、`status`、`phase`、`stat_date`、`started_at`、`finished_at`
- 统计字段：`request_count`、`area_count`、`row_count`、`current_upsert_count`、`snapshot_insert_count`、`acc_upsert_count`
- 排查字段：`structure_change_summary`，保存本批次结构变化摘要 JSON

### `area`
- 主键：`id`
- 唯一：`(level_type, area_code)`
- 主要字段：`area_code`、`area_name`、`level_type`、`level_no`、`parent_id`、`enabled`、`sort_order`

### `indicator`
- 主键：`id`
- 唯一：`code`
- 主要字段：`code`、`name`、`enabled`、`sort_order`

### `request_target`
- 主键：`id`
- 唯一：`(target_type, target_code)`
- 主要字段：`target_code`、`target_name`、`target_type`、`area_id`、`parent_target_id`、`enabled`、`sort_order`
- 类型边界：只保存 `CITY / BRANCH / GRID / CHANNEL_MANAGER`，不保存 `CHANNEL`

### `channel_manager_area`
- 主键：`id`
- 唯一：`(manager_target_id, channel_area_id)`
- 主要字段：`manager_target_id`、`channel_area_id`、`grid_area_id`、`enabled`、`last_seen_at`、`missing_count`
- 用途：保存 `CHANNEL_MANAGER -> CHANNEL` 的真实关系，用于识别渠道新增、减少、换经理
- 注意：`CHANNEL` 仍只在 `area` 表中保存，不进入 `request_target`
- 普通关系缺失按 `relation_missing_disable_threshold` 连续确认后禁用；渠道转移和 manager 删除会立即校准关系

### `metric_current`
- 主键：`id`
- 唯一：`(area_id, indicator_id)`
- 主要字段：`metric_value`、`stat_date`、`collected_at`、`collection_run_id`
- 索引：`indicator_id, metric_value`；`stat_date, indicator_id`

### `metric_snapshot`
- 主键：`id`
- 唯一：`(collection_run_id, area_id, indicator_id)`
- 主要字段：`metric_value`、`collected_at`
- 索引：`area_id, indicator_id, collected_at`

### `metric_acc`
- 主键：`id`
- 唯一：`(period_type, stat_date, area_id, indicator_id)`
- 主要字段：`period_type`、`stat_date`、`metric_value`、`collection_run_id`、`collected_at`
- 索引：`area_id, indicator_id, period_type, stat_date`

### `metric_target`
- 主键：`id`
- 唯一：`(period_type, area_id, indicator_id)`
- `period_type` 支持 `REALTIME` / `DAY_ACC` / `MONTH`
- 主要字段：`area_id`、`indicator_id`、`target_value`、`enabled`
- 用途：前端完成进度计算。有目标值时，完成进度 = 完成量 / 目标值；没有目标值时显示 `--`

> `dashboard_wide` 宽表已在 `20260618_0016` 迁移中移除。当前前端接口直接查询规范化源表；后续如确实需要读性能优化，再重新设计物化表结构。

## 4. 我实际连到的库

- 数据库：`dashboard`
- MySQL 版本：`8.4.8`
- 已读取表：`SHOW TABLES`
- 已逐表执行 `DESCRIBE`

## 5. 代码入口速查

- `backend/routers/dashboard.py`
- `infrastructure/dashboard_mysql.py`
- `services/dashboard_query_service.py`
- `services/dashboard_batch_runner.py`
- `services/dashboard_collection_orchestrator.py`
- `flows/dashboard_metric_flow.py`
- `migrations/dashboard/env.py`

## 6. 接口与表映射

- `GET /api/dashboard/overview` -> `get_dashboard_overview_fast()`；读 `area`、`indicator`、`metric_current`、`metric_snapshot`、`metric_acc`、`metric_target`
- `GET /api/dashboard/drill-down` -> `get_drill_down()`；按父级区域下钻，读规范化源表
- `GET /api/dashboard/current` -> `get_current_wide_table()`；直接读 `area`、`indicator`、`metric_current`，并补充 `collection_run`
- `GET /api/dashboard/current-with-changes` -> `get_current_with_changes()`；在 `current` 基础上额外读 `metric_snapshot`
- `GET /api/dashboard/acc` -> `get_acc_wide_table()`；读 `area`、`indicator`、`metric_acc`
- `GET /api/dashboard/trend` -> `get_snapshot_trend()`；读 `indicator`、`metric_snapshot`
- `POST /api/dashboard/collect` -> 提交 Prefect 采集流程；采集阶段会更新 `collection_run`，并写入 `metric_current`、`metric_snapshot`、`metric_acc`，同时维护 `area` / `request_target` / `channel_manager_area`
