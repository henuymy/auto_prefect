# 驾驶舱历史时间语义完善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保留 V1 历史批次与正负 3 分钟快照规则，同时让 API 和页面明确区分采集档位、批次开始时间与批次完成时间。

**Architecture:** 后端只在既有 `history_meta` 中增加两个自描述字段，不改变任何查询条件；前端消费这些字段并调整历史时间栏文案。回归测试分别锁定后端契约、V1 兼容计算行为和页面文字契约。

**Tech Stack:** Python 3.11+、SQLAlchemy 2、pytest、React 19、TypeScript 5.8、Vite 6

## Global Constraints

- 历史批次继续按 `started_at` 归属采集档位。
- 主历史值继续读取所选批次完成时间之前的快照。
- 变化基线继续使用 `CHANGE_WINDOW_TOLERANCE_MINUTES = 3` 和稀疏快照向前取值规则。
- 快照存在性子查询本次只记录评估结论，不修改查询行为。
- 不引入新的前端测试框架或运行时依赖。

---

### Task 1: 扩展历史元数据接口契约

**Files:**
- Modify: `tests/test_dashboard_v2_query_service.py:539-565`
- Modify: `services/dashboard_v2_query_service.py:674-692`

**Interfaces:**
- Consumes: `CHANGE_WINDOW_TOLERANCE_MINUTES: int` from `services.dashboard_query_service`
- Produces: `history_meta.time_basis: Literal["BATCH_STARTED_AT"]`
- Produces: `history_meta.change_tolerance_minutes: int`

- [ ] **Step 1: 写入失败的接口契约测试**

在 `test_historical_time_uses_v1_completed_batch_and_started_anchor` 末尾增加：

```python
    assert result["history_meta"]["time_basis"] == "BATCH_STARTED_AT"
    assert result["history_meta"]["change_tolerance_minutes"] == 3
```

- [ ] **Step 2: 运行测试并确认因字段缺失而失败**

Run:

```powershell
python -m pytest tests/test_dashboard_v2_query_service.py::test_historical_time_uses_v1_completed_batch_and_started_anchor -q
```

Expected: FAIL，错误为 `KeyError: 'time_basis'`。

- [ ] **Step 3: 最小化扩展 `_history_meta` 返回值**

在 `services/dashboard_v2_query_service.py` 的 `_history_meta` 返回字典中加入：

```python
        "time_basis": "BATCH_STARTED_AT",
        "change_tolerance_minutes": CHANGE_WINDOW_TOLERANCE_MINUTES,
```

不修改 `_historical_run`、`value_cutoff`、`change_anchor` 或快照查询。

- [ ] **Step 4: 运行单测并确认通过**

Run:

```powershell
python -m pytest tests/test_dashboard_v2_query_service.py::test_historical_time_uses_v1_completed_batch_and_started_anchor -q
```

Expected: `1 passed`。

- [ ] **Step 5: 运行 V1/V2 历史兼容回归测试**

Run:

```powershell
python -m pytest tests/test_dashboard_query_service.py tests/test_dashboard_v2_query_service.py -q
```

Expected: 全部通过；现有“完成较晚按开始时间归档”和正负 3 分钟最近快照断言不变。

- [ ] **Step 6: 提交后端契约变更**

```powershell
git add services/dashboard_v2_query_service.py tests/test_dashboard_v2_query_service.py
git commit -m "feat: 明确历史批次时间语义"
```

### Task 2: 明确前端采集档位文案

**Files:**
- Create: `tests/test_dashboard_v2_ui_contract.py`
- Modify: `admin_react/src/types/dashboard.ts:230-236`
- Modify: `admin_react/src/dashboard/DashboardCockpit.tsx:2197-2217`
- Modify: `admin_react/src/dashboard/DashboardCockpit.tsx:2531-2660`

**Interfaces:**
- Consumes: `DashboardHistoryMeta.time_basis`
- Consumes: `DashboardHistoryMeta.change_tolerance_minutes`
- Produces: 页面文案“采集档位（批次开始时间）”“匹配批次开始时间”“批次完成时间”

- [ ] **Step 1: 写入失败的页面文字契约测试**

创建 `tests/test_dashboard_v2_ui_contract.py`：

```python
from pathlib import Path


COCKPIT = Path("admin_react/src/dashboard/DashboardCockpit.tsx")


def test_history_time_copy_distinguishes_batch_start_and_finish():
    source = COCKPIT.read_text(encoding="utf-8")

    assert "采集档位（批次开始时间）" in source
    assert "匹配批次开始时间" in source
    assert "批次完成时间" in source
    assert "change_tolerance_minutes" in source
```

- [ ] **Step 2: 运行测试并确认页面尚未包含新语义**

Run:

```powershell
python -m pytest tests/test_dashboard_v2_ui_contract.py -q
```

Expected: FAIL，首个缺失字符串为“采集档位（批次开始时间）”。

- [ ] **Step 3: 扩展 TypeScript 历史元数据类型**

在 `DashboardHistoryMeta` 中加入：

```typescript
  time_basis: "BATCH_STARTED_AT";
  change_tolerance_minutes: number;
```

- [ ] **Step 4: 修改历史模式标题、选择器和结果文案**

在 `DashboardCockpit.tsx` 中完成以下文字调整：

```tsx
// 单指标区域标题
"历史采集档位"

// 区域说明
"按批次开始时间选择；展示该批次完成后的历史快照"

// 历史模式按钮
"历史档位"

// 日期/时间选择器标签
<span>采集档位（批次开始时间）</span>

// 匹配结果
匹配批次开始时间 <strong>...</strong>

// 完成时间
批次完成时间 ...
```

在历史说明区域使用接口值展示容差，保留目标配置说明：

```tsx
<span className="history-target-basis">
  变化基线：理论时间前后 ±{historyMeta?.change_tolerance_minutes ?? 3} 分钟内最近快照；目标：当前配置
</span>
```

- [ ] **Step 5: 运行页面文字契约测试**

Run:

```powershell
python -m pytest tests/test_dashboard_v2_ui_contract.py -q
```

Expected: `1 passed`。

- [ ] **Step 6: 运行 TypeScript 类型检查和生产构建**

Run:

```powershell
npm run typecheck
npm run build
```

Workdir: `admin_react`

Expected: 两条命令退出码均为 0，Vite 成功生成 `dist`。

- [ ] **Step 7: 提交前端语义变更**

```powershell
git add admin_react/src/types/dashboard.ts admin_react/src/dashboard/DashboardCockpit.tsx tests/test_dashboard_v2_ui_contract.py
git commit -m "fix: 标明历史采集档位时间"
```

### Task 3: 完整验证与契约复核

**Files:**
- Verify: `docs/superpowers/specs/2026-07-10-dashboard-history-time-semantics-design.md`
- Verify: all changed production and test files

**Interfaces:**
- Consumes: Task 1 and Task 2 outputs
- Produces: 可交付的验证证据

- [ ] **Step 1: 运行完整 Python 测试**

Run:

```powershell
python -m pytest -q
```

Expected: 无失败；真实 MySQL 测试可按既有条件跳过。

- [ ] **Step 2: 运行 Python 静态检查**

Run:

```powershell
python -m ruff check .
```

Expected: `All checks passed!`。

- [ ] **Step 3: 重新运行前端构建**

Run:

```powershell
npm run build
```

Workdir: `admin_react`

Expected: TypeScript 和 Vite 构建退出码为 0。

- [ ] **Step 4: 检查差异没有改变历史查询语句**

Run:

```powershell
git diff HEAD~2 -- services/dashboard_v2_query_service.py
git diff --check
```

Expected: 后端生产代码仅增加两个 `history_meta` 字段；没有修改 `_historical_run`、`_value_rows_at` 或 `_change_value_rows_at`。

- [ ] **Step 5: 对照规格逐项验收**

确认以下结果：

```text
API 返回 BATCH_STARTED_AT 和容差 3
页面明确区分采集档位、批次开始时间和批次完成时间
现有 V1/V2 历史兼容测试保持通过
快照存在性子查询未发生代码变更
```
