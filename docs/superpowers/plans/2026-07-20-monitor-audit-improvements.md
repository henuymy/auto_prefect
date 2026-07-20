# Monitor Audit Improvements Implementation Plan
> **归档状态：** 本文记录当时的需求、设计或实施计划；当前有效的运行契约以 [运行监控中心设计](../../run-monitoring-center-design.md) 和 [Webhook 运维手册](../../operations/prefect-monitor-webhook.md) 为准。
>


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve failure diagnosis, filter scope clarity, accessible detail modes, and 30-day monitor browsing.

**Architecture:** Keep the existing FastAPI snapshot and WebSocket contracts. Frontend derives bounded visible batches from the existing report-only run list, while the backend schema continues providing a sanitized failure summary for every failed run.

**Tech Stack:** FastAPI, Python pytest, React, TypeScript, Vitest, Testing Library.

## Global Constraints

- Retain report-only and 30-day monitoring scope.
- Do not expose Prefect secrets or unsanitized logs.
- Keep WebSocket updates incremental and avoid extra snapshot requests.
- Work in the current workspace and do not create commits.

---

### Task 1: Failure-first detail content

**Files:**
- Modify: `frontend/src/monitor/RunDetailDrawer.tsx`
- Modify: `frontend/src/monitor/RunDetailDrawer.test.tsx`

- [ ] Add a failing test that expects the default business view of a failed run to show its business summary and failure step.
- [ ] Run the focused Vitest test and verify it fails.
- [ ] Render the summary above progress and preserve the unavailable-detail notice.
- [ ] Re-run the focused test.

### Task 2: Scope and accessibility semantics

**Files:**
- Modify: `frontend/src/monitor/MonitorCenter.tsx`
- Modify: `frontend/src/monitor/RunDetailDrawer.tsx`
- Modify: `frontend/src/monitor/MonitorCenter.test.tsx`
- Modify: `frontend/src/monitor/RunDetailDrawer.test.tsx`

- [ ] Add failing tests for a global pending label and selected mode semantics.
- [ ] Run focused tests and verify the expected failures.
- [ ] Implement the label and `aria-pressed` mode controls.
- [ ] Re-run focused tests.

### Task 3: Bounded history views and freshness context

**Files:**
- Modify: `frontend/src/monitor/MonitorCenter.tsx`
- Modify: `frontend/src/monitor/time.ts`
- Modify: `frontend/src/monitor/MonitorCenter.test.tsx`
- Modify: `frontend/src/monitor/monitor.css`

- [ ] Add failing tests for pagination controls and relative upstream timing.
- [ ] Run focused tests and verify expected failures.
- [ ] Add load-more batches and relative-time labels without changing stream fetch behavior.
- [ ] Re-run focused tests and the monitor test suite.

### Task 4: Regression and browser verification

**Files:**
- Verify: `tests/test_monitor_api_schema.py`
- Verify: `tests/test_prefect_monitor_detail_service.py`
- Verify: `frontend/src/monitor/*.test.tsx`

- [ ] Run backend monitoring tests.
- [ ] Run frontend typecheck, tests, and production build.
- [ ] Verify failure detail, filter scope, load-more, and keyboard closure in the open monitor page.
