# 腾讯文档自动读取范围优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让腾讯文档下载的自动范围使用实际数据边界，同时严格保留用户指定的显式范围。

**Architecture:** 范围解析继续集中在 `resolve_sheet_range()`。自动模式优先使用元数据中的 `rowCount` 与 `columnCount`，缺失时回退到 `rowTotal` 与 `columnTotal`；显式 A1 范围只进行语法校验，不再按元数据裁剪。调用层、分块读取和无效范围处理不变。

**Tech Stack:** Python 3、pytest、requests、openpyxl。

## Global Constraints

- 不新增配置项或第三方依赖。
- 自动模式包括空字符串、`auto`、`used`、`used_range`。
- 显式 A1 范围必须按用户填写值请求。
- 维持现有范围无效后的分块停止逻辑。

---

## File Structure

- Modify: `services/tencent_sheet_service.py` - 解析腾讯 Sheet 元数据边界和 A1 读取范围。
- Modify: `tests/test_tencent_sheet_service.py` - 覆盖自动范围、容量回退和显式范围的契约。

### Task 1: 定义并验证范围解析契约

**Files:**
- Modify: `tests/test_tencent_sheet_service.py:106-112`

**Interfaces:**
- Consumes: `resolve_sheet_range(range_address: str, sheet_property: dict[str, Any] | None) -> tuple[str, bool]`
- Produces: 范围解析的测试契约，供 Task 2 实现。

- [ ] **Step 1: 写入失败测试**

将现有 `test_resolve_sheet_range_can_auto_detect_or_clamp_to_sheet_bounds` 替换为：

```python
def test_resolve_sheet_range_prefers_used_bounds_and_preserves_explicit_range():
    sheet_property = {
        "rowTotal": 200,
        "columnTotal": 26,
        "rowCount": 2,
        "columnCount": 1,
    }

    assert resolve_sheet_range("auto", sheet_property) == ("A1:A2", True)
    assert resolve_sheet_range("", sheet_property) == ("A1:A2", True)
    assert resolve_sheet_range("used", sheet_property) == ("A1:A2", True)
    assert resolve_sheet_range("A1:Z1000", sheet_property) == ("A1:Z1000", False)
    assert resolve_sheet_range("A1:B20", sheet_property) == ("A1:B20", False)

def test_resolve_sheet_range_falls_back_to_total_bounds_when_used_bounds_are_missing():
    sheet_property = {"rowTotal": 200, "columnTotal": 26}

    assert resolve_sheet_range("auto", sheet_property) == ("A1:Z200", True)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_tencent_sheet_service.py -k resolve_sheet_range -v`

Expected: `test_resolve_sheet_range_prefers_used_bounds_and_preserves_explicit_range` 失败，实际 `auto` 仍为 `A1:Z200`，显式范围仍被裁剪为 `A1:Z200`。

### Task 2: 实现最小范围解析修改

**Files:**
- Modify: `services/tencent_sheet_service.py:287-328`
- Test: `tests/test_tencent_sheet_service.py:106-130`

**Interfaces:**
- Consumes: Task 1 的 `resolve_sheet_range()` 契约。
- Produces: 对自动范围返回实际数据边界，对显式范围原样返回且标记为未自动调整。

- [ ] **Step 1: 实现元数据边界优先级**

将 `sheet_bounds_from_property()` 改为先验证完整的实际边界对，再验证完整的容量边界对：

```python
def sheet_bounds_from_property(sheet_property: dict[str, Any] | None) -> tuple[int, int] | None:
    if not sheet_property:
        return None
    for row_key, column_key in (("rowCount", "columnCount"), ("rowTotal", "columnTotal"), ("rows", "columns")):
        try:
            rows = int(sheet_property.get(row_key))
            columns = int(sheet_property.get(column_key))
        except (TypeError, ValueError):
            continue
        if rows >= 1 and columns >= 1:
            return rows, columns
    return None
```

- [ ] **Step 2: 保留显式范围**

在 `resolve_sheet_range()` 中，将解析后的显式范围直接返回：

```python
    start_col, start_row, end_col, end_row = parse_a1_range(text)
    return format_a1_range(start_col, start_row, end_col, end_row), False
```

删除显式范围分支中读取 `bounds`、裁剪终点和设置 `range_auto_adjusted` 的代码；自动范围分支保持对 `sheet_bounds_from_property()` 的调用。

- [ ] **Step 3: 运行范围解析测试并确认通过**

Run: `python -m pytest tests/test_tencent_sheet_service.py -k resolve_sheet_range -v`

Expected: 2 passed。

- [ ] **Step 4: 运行腾讯文档服务完整测试**

Run: `python -m pytest tests/test_tencent_sheet_service.py -v`

Expected: 全部通过；现有的分块与无效范围用例继续覆盖显式范围大于实际边界时的兼容行为。

- [ ] **Step 5: 提交实现**

```bash
git add services/tencent_sheet_service.py tests/test_tencent_sheet_service.py
git commit -m "fix: optimize Tencent sheet auto ranges"
```
