# Dashboard Historical Daily Accumulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow cockpit cumulative mode to query daily accumulation data for a user-selected date.

**Architecture:** Store the cumulative date separately from the history snapshot timestamp in `DashboardCockpit`. Pass it to the existing `getAccDashboard` `statDate` argument, then extend `HistoryTimeBar` with a cumulative-only date input and query button.

**Tech Stack:** React 19, TypeScript, Vite, Python pytest static UI-contract test.

## Global Constraints

- Only `DAY_ACC` is selectable; no month selector is added.
- Use the existing `GET /api/dashboard/acc?period_type=DAY_ACC&stat_date=YYYY-MM-DD` API.
- Do not calculate or fall back values in the browser.
- Do not alter realtime, realtime-accumulation, or history-snapshot behavior.

---

### Task 1: Retain and submit the selected date

**Files:**

- Modify: `tests/test_dashboard_v2_ui_contract.py`
- Modify: `admin_react/src/dashboard/DashboardCockpit.tsx:1000-1030,1310-1340,2130-2170`

**Interfaces:**

- Consumes: `getAccDashboard(periodType, statDate, nodeType, parentId, indicatorCodes, targetScenario)`.
- Produces: `cumulativeAsOf: string` used as the second `getAccDashboard` argument in cumulative mode and as the cumulative-mode cache key date.

- [ ] **Step 1: Write the failing test**

```python
def test_cumulative_mode_queries_selected_daily_accumulation_date():
    source = COCKPIT.read_text(encoding="utf-8")
    assert 'const [cumulativeAsOf, setCumulativeAsOf] = useState("");' in source
    assert 'const [cumulativeInput, setCumulativeInput] = useState("");' in source
    assert '"DAY_ACC",\n            cumulativeAsOf || undefined,' in source
    assert 'cumulativeValue={cumulativeInput}' in source
    assert 'asOf: dataTimeMode === "history" ? historyAsOf : cumulativeAsOf || null,' in source
```

- [ ] **Step 2: Verify RED**

Run `python -m pytest tests/test_dashboard_v2_ui_contract.py -q`; expect failure because cumulative date state and API wiring are absent.

- [ ] **Step 3: Implement the minimum change**

Add `cumulativeAsOf` (submitted date) and `cumulativeInput` (editable date) next to historical state. Replace the cumulative request's second argument `undefined` with `cumulativeAsOf || undefined`. Include the submitted date in `makeDashboardCacheKey` by setting `asOf: dataTimeMode === "history" ? historyAsOf : cumulativeAsOf || null`. When the implicit default request returns `acc.stat_date`, set both empty states to that date so the effective date is visible. Pass `cumulativeValue={cumulativeInput}` to `HistoryTimeBar`.

- [ ] **Step 4: Verify GREEN**

Run `python -m pytest tests/test_dashboard_v2_ui_contract.py -q`; expect PASS.

- [ ] **Step 5: Commit**

Run `git add tests/test_dashboard_v2_ui_contract.py admin_react/src/dashboard/DashboardCockpit.tsx` then `git commit -m "feat: retain selected daily accumulation date"`.

### Task 2: Add cumulative date UI and explicit query action

**Files:**

- Modify: `tests/test_dashboard_v2_ui_contract.py`
- Modify: `admin_react/src/dashboard/DashboardCockpit.tsx:2470-2670`

**Interfaces:**

- Consumes: `cumulativeValue`, `onCumulativeChange`, `onCumulativeQuery` props from `DashboardCockpit`.
- Produces: a date input labelled `累计日期` and cumulative-mode query button.

- [ ] **Step 1: Write the failing test**

```python
def test_cumulative_mode_exposes_a_daily_accumulation_date_query_control():
    source = COCKPIT.read_text(encoding="utf-8")
    assert 'aria-label="累计日期"' in source
    assert 'type="date"' in source
    assert 'onCumulativeQuery' in source
```

- [ ] **Step 2: Verify RED**

Run `python -m pytest tests/test_dashboard_v2_ui_contract.py -q`; expect failure because the input and callback are absent.

- [ ] **Step 3: Implement the minimum change**

Add `cumulativeValue: string`, `onCumulativeChange: (value: string) => void`, and `onCumulativeQuery: () => void` to `HistoryTimeBar`. When `mode === "cumulative"`, render a `type="date"` input with `aria-label="累计日期"`, bound to `cumulativeValue`, plus the existing search-style button calling `onCumulativeQuery`. Pass `setCumulativeInput` as the change callback. On query, set `cumulativeAsOf` from `cumulativeInput`; if it is unchanged, call `fetchData(true)` so the user can explicitly refresh the selected date.

- [ ] **Step 4: Verify GREEN**

Run `python -m pytest tests/test_dashboard_v2_ui_contract.py -q`; expect PASS.

- [ ] **Step 5: Commit**

Run `git add tests/test_dashboard_v2_ui_contract.py admin_react/src/dashboard/DashboardCockpit.tsx` then `git commit -m "feat: add historical daily accumulation picker"`.

### Task 3: Verify the deliverable

**Files:**

- Verify: `tests/test_dashboard_v2_ui_contract.py`
- Verify: `admin_react/src/dashboard/DashboardCockpit.tsx`

- [ ] **Step 1: Run focused UI tests**

Run `python -m pytest tests/test_dashboard_v2_ui_contract.py -q`; expect PASS.

- [ ] **Step 2: Run the frontend production build**

Run `npm run build` from `admin_react`; expect `tsc -b && vite build` exit code 0.

- [ ] **Step 3: Check final changes**

Run `git diff --check HEAD~2..HEAD` and `git status --short`; expect no whitespace errors or unintended changes.
