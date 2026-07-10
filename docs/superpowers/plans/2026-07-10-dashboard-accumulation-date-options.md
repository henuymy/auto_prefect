# Dashboard Accumulation Date Options Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display only persisted daily-accumulation dates in a lazy-loaded cumulative-mode selector.

**Architecture:** Add a V2 query-service method and cached router endpoint that page distinct `DAY_ACC` dates in descending order. The React cockpit requests the first page on cumulative-mode entry and requests subsequent pages from the selector scroll boundary.

**Tech Stack:** FastAPI, SQLAlchemy, React 19, TypeScript, pytest.

## Global Constraints

- Dates are distinct `MetricAccV2.stat_date` values for `period_type = DAY_ACC`, newest first.
- Default and maximum page size are 50.
- Existing accumulation query endpoint remains the data source after selection.
- No arbitrary date entry, browser-side accumulation, or changes to other time modes.

---

### Task 1: Paginated available-date API

**Files:**

- Modify: `services/dashboard_v2_query_service.py`
- Modify: `backend/routers/dashboard.py`
- Modify: `tests/test_dashboard_v2_query_service.py`
- Modify: `tests/test_dashboard_router.py`

- [ ] **Step 1: Write failing query-service and router tests**

```python
def test_acc_options_returns_distinct_daily_dates_in_descending_pages():
    result = get_acc_options(_engine(), page=1, page_size=2)
    assert result == {
        "dates": ["2026-06-30", "2026-06-29"],
        "page": 1, "page_size": 2, "total": 3, "has_more": True,
    }
```

```python
def test_acc_options_route_forwards_pagination(monkeypatch):
    monkeypatch.setattr(dashboard, "get_acc_options", lambda engine, **kw: kw)
    assert dashboard.dashboard_acc_options(page=2, page_size=50) == {"page": 2, "page_size": 50}
```

- [ ] **Step 2: Verify RED**

Run `python -m pytest tests/test_dashboard_v2_query_service.py tests/test_dashboard_router.py -q`; expect import/attribute failures because the method and route do not exist.

- [ ] **Step 3: Implement minimal API**

Implement `get_acc_options(engine, *, page: int = 1, page_size: int = 50)`. Select distinct `MetricAccV2.stat_date`, filter `DAY_ACC`, order descending, offset `(page - 1) * page_size`, limit `page_size`, and calculate total with `count(distinct(...))`. Return ISO dates and `has_more = page * page_size < total`. Import it in the router and add `@router.get("/acc/options")` with `Query(1, ge=1)` and `Query(50, ge=1, le=50)`, reusing `_cached_response` and existing SQLAlchemy 503 handling.

- [ ] **Step 4: Verify GREEN**

Run `python -m pytest tests/test_dashboard_v2_query_service.py tests/test_dashboard_router.py -q`; expect PASS.

- [ ] **Step 5: Commit**

Run `git add services/dashboard_v2_query_service.py backend/routers/dashboard.py tests/test_dashboard_v2_query_service.py tests/test_dashboard_router.py` then `git commit -m "feat: add paginated accumulation date options"`.

### Task 2: Lazy cumulative-date selector

**Files:**

- Modify: `admin_react/src/types/dashboard.ts`
- Modify: `admin_react/src/lib/api.ts`
- Modify: `admin_react/src/dashboard/DashboardCockpit.tsx`
- Modify: `tests/test_dashboard_v2_ui_contract.py`

- [ ] **Step 1: Write failing UI contract test**

```python
def test_cumulative_mode_uses_lazy_available_date_selector():
    source = COCKPIT.read_text(encoding="utf-8")
    assert 'getDashboardAccOptions' in source
    assert 'cumulativeDateOptions' in source
    assert 'aria-label="累计日期"' in source
    assert 'onCumulativeLoadMore' in source
```

- [ ] **Step 2: Verify RED**

Run `python -m pytest tests/test_dashboard_v2_ui_contract.py -q`; expect failure because the lazy options state and callback do not exist.

- [ ] **Step 3: Implement minimal selector**

Add `DashboardAccOptionsResponse` and `getDashboardAccOptions(page = 1, pageSize = 50)`. In `DashboardCockpit`, retain date options, current page, `has_more`, and loading state; load page one when entering cumulative mode and append a later page only once. Replace the cumulative `type="date"` control with a `<select aria-label="累计日期">` populated from loaded dates. Pass a scroll callback that loads another page when the select popup scroll position reaches its bottom. Default the selected/submitted date to the first returned date only when no date is already selected.

- [ ] **Step 4: Verify GREEN**

Run `python -m pytest tests/test_dashboard_v2_ui_contract.py -q`; expect PASS.

- [ ] **Step 5: Commit**

Run `git add admin_react/src/types/dashboard.ts admin_react/src/lib/api.ts admin_react/src/dashboard/DashboardCockpit.tsx tests/test_dashboard_v2_ui_contract.py` then `git commit -m "feat: lazy load accumulation date selector"`.

### Task 3: Verify

- [ ] Run `python -m pytest tests/test_dashboard_v2_query_service.py tests/test_dashboard_router.py tests/test_dashboard_v2_ui_contract.py -q`; expect PASS.
- [ ] Run `npm run build` from `admin_react`; record pre-existing failures separately if they still block the build.
- [ ] Run `git diff --check` and inspect `git status --short`.
