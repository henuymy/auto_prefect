# 通报运行记录 30 天 Implementation Plan
> **归档状态：** 本文记录当时的需求、设计或实施计划；当前有效的运行契约以 [运行监控中心设计](../../run-monitoring-center-design.md) 和 [Webhook 运维手册](../../operations/prefect-monitor-webhook.md) 为准。
>


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 运行监控中心仅展示最近 30 天的通报实际运行，同时保留独立的待执行队列，并将已结束监控记录的保留期设为 30 天。

**Architecture:** 后端快照在序列化前按“通报任务、非 Scheduled、30 天滚动窗口”筛选运行历史，待执行队列继续从未筛选的原始运行集合生成。驾驶舱每日维护任务在既有 MySQL 锁中删除超过保留期的已结束监控运行，外键级联删除步骤和日志。前端数据服务以同一规则防御性过滤快照，页面的时间线、表格和状态汇总共享该结果。

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, Prefect, pytest, React 19, TypeScript, Vitest, Testing Library.

## Global Constraints

- 历史窗口从快照的当前时刻向前滚动 30 天；状态为 `scheduled` 的记录绝不进入历史、时间线或状态汇总。
- 只有任务名称以 `通报 · ` 开头的记录是本页历史记录；数据仓库、驾驶舱、`session-keeper` 和网页操作不能混入。
- 待执行队列继续以未过滤的 Scheduled 运行作为独立字段返回和展示。
- 清理仅删除已结束的 `succeeded`、`failed` 或 `cancelled` 运行；`running` 和 `scheduled` 记录保留。
- Python 测试使用 `python -m pytest -p no:cacheprovider`，不能直接调用 `pytest` 入口。

---

### Task 1: Filter the API snapshot to report execution history

**Files:**
- Modify: `backend/routers/monitor.py`
- Create: `tests/test_monitor_router.py`

**Interfaces:**
- Produces: `REPORT_HISTORY_DAYS = 30`, `report_history_runs(runs, now)` and `_snapshot_payload(runs, now=None)`.
- Consumes: normalized store dictionaries with `task_name`, `status`, `started_at`, `scheduled_at` and ISO timestamps.

- [ ] **Step 1: Write the failing snapshot contract test**

```python
from datetime import datetime, timezone

from backend.routers.monitor import _snapshot_payload


def test_snapshot_returns_only_recent_report_executions_and_keeps_all_scheduled_items():
    runs = [
        {"id": "recent-report", "task_name": "通报 · 当日经营日报", "status": "succeeded", "source": "prefect", "started_at": "2026-07-18T09:00:00+08:00", "scheduled_at": "2026-07-18T08:59:00+08:00"},
        {"id": "old-report", "task_name": "通报 · 过期日报", "status": "failed", "source": "prefect", "started_at": "2026-06-17T09:00:00+08:00"},
        {"id": "session", "task_name": "session-keeper", "status": "succeeded", "source": "prefect", "started_at": "2026-07-18T09:00:00+08:00"},
        {"id": "scheduled-report", "task_name": "通报 · 明日经营日报", "status": "scheduled", "source": "prefect", "scheduled_at": "2026-07-19T09:00:00+08:00"},
        {"id": "scheduled-dashboard", "task_name": "驾驶舱采集 · 指标", "status": "scheduled", "source": "prefect", "scheduled_at": "2026-07-19T10:00:00+08:00"},
    ]

    payload = _snapshot_payload(runs, now=datetime(2026, 7, 18, 12, tzinfo=timezone.utc))

    assert [item["id"] for item in payload["runs"]] == ["recent-report"]
    assert payload["summary"] == {"succeeded": 1, "running": 0, "failed": 0, "scheduled": 0}
    assert [item["id"] for item in payload["pendingQueue"]["items"]] == ["scheduled-report", "scheduled-dashboard"]
```

- [ ] **Step 2: Run the API contract test and verify it fails**

Run: `python -m pytest -p no:cacheprovider tests/test_monitor_router.py -q`

Expected: FAIL because the current snapshot includes the old, non-report and Scheduled runs in `runs`.

- [ ] **Step 3: Implement the shared history predicate and snapshot filtering**

```python
REPORT_HISTORY_DAYS = 30
REPORT_TASK_PREFIX = "通报 · "


def _as_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def report_history_runs(runs: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    cutoff = now.astimezone(timezone.utc) - timedelta(days=REPORT_HISTORY_DAYS)
    return [
        run
        for run in runs
        if str(run.get("task_name") or "").startswith(REPORT_TASK_PREFIX)
        and run.get("status") != "scheduled"
        and (_as_utc(run.get("started_at") or run.get("scheduled_at")) or cutoff) >= cutoff
    ]


def _snapshot_payload(runs: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    updated_at = now or datetime.now(timezone.utc)
    history = report_history_runs(runs, now=updated_at)
    return {
        "runs": [to_frontend_run(run) for run in history],
        "pendingQueue": to_frontend_pending_queue(runs),
        "summary": {
            "succeeded": sum(run["status"] == "succeeded" for run in history),
            "running": sum(run["status"] == "running" for run in history),
            "failed": sum(run["status"] == "failed" for run in history),
            "scheduled": 0,
        },
        "updatedAt": updated_at.isoformat(timespec="seconds"),
        "connected": True,
    }
```

Import `timedelta` and `timezone`, and make `/snapshot`, `/summary` and `/stream` call the updated helper without a test clock.

- [ ] **Step 4: Run the API contract tests and verify they pass**

Run: `python -m pytest -p no:cacheprovider tests/test_monitor_router.py tests/test_monitor_api_schema.py -q`

Expected: PASS; the queue keeps both Scheduled entries while the history and summary contain only the current report execution.

### Task 2: Retain finished monitor data for 30 days

**Files:**
- Modify: `services/dashboard_v2_retention_service.py`
- Modify: `config/dashboard/session.json`
- Modify: `tests/test_dashboard_v2_retention_service.py`

**Interfaces:**
- Consumes: `retention.monitor_run_retention_days` from the existing dashboard maintenance configuration.
- Produces: a `monitor_run_deleted` count in `cleanup_v2_expired_data()` and `execute_v2_retention_maintenance()` results.

- [ ] **Step 1: Write the failing retention test**

```python
def test_v2_cleanup_keeps_active_monitor_runs_and_removes_finished_monitor_runs_after_30_days():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE monitor_runs (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, external_run_id TEXT NOT NULL,
                task_name TEXT NOT NULL, trigger TEXT NOT NULL, status TEXT NOT NULL,
                finished_at DATETIME, current_step TEXT NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO monitor_runs (id, source, external_run_id, task_name, trigger, status, finished_at, current_step)
            VALUES
                ('expired', 'prefect', 'expired', '通报 · 过期日报', '定时调度', 'succeeded', '2026-05-31 11:00:00', '已完成'),
                ('active', 'prefect', 'active', '通报 · 仍在运行', '定时调度', 'running', NULL, '下载报表')
        """))

    with Session(engine) as session:
        result = cleanup_v2_expired_data(session, now=datetime(2026, 6, 30, 12), monitor_run_retention_days=30)

    assert result["monitor_run_deleted"] == 1
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM monitor_runs WHERE id = 'expired'")) == 0
        assert connection.scalar(text("SELECT COUNT(*) FROM monitor_runs WHERE id = 'active'")) == 1
```

- [ ] **Step 2: Run the retention test and verify it fails**

Run: `python -m pytest -p no:cacheprovider tests/test_dashboard_v2_retention_service.py -q`

Expected: FAIL because `cleanup_v2_expired_data()` has no monitor retention parameter or deletion count.

- [ ] **Step 3: Add monitor cleanup to the existing locked maintenance transaction**

```python
from models.monitor import MonitorRun

# cleanup_v2_expired_data signature
monitor_run_retention_days: int = 30,

monitor_days = _bounded_int(
    monitor_run_retention_days, "monitor_run_retention_days", 1, _MAX_RETENTION_DAYS
)
monitor_deleted = int(session.execute(
    delete(MonitorRun).where(
        MonitorRun.finished_at < resolved_now - timedelta(days=monitor_days),
        MonitorRun.status.in_(["succeeded", "failed", "cancelled"]),
    )
).rowcount or 0)

# returned result
"monitor_run_deleted": monitor_deleted,
```

Pass `config.get("monitor_run_retention_days", 30)` from `execute_v2_retention_maintenance()`, and add the following entry to `config/dashboard/session.json`:

```json
"monitor_run_retention_days": 30
```

Place the configuration key inside the existing `retention` object. The existing daily Prefect maintenance task already calls this service while holding the dashboard MySQL lock, so no new schedule or read-path deletion is added.

- [ ] **Step 4: Run the retention tests and verify they pass**

Run: `python -m pytest -p no:cacheprovider tests/test_dashboard_v2_retention_service.py -q`

Expected: PASS; expired completed monitor records are deleted, active records remain, and the existing dashboard retention assertions stay green.

### Task 3: Apply the same history contract in the monitor UI

**Files:**
- Modify: `frontend/src/monitor/mockMonitorService.ts`
- Modify: `frontend/src/monitor/mockMonitorService.test.ts`
- Modify: `frontend/src/monitor/MonitorCenter.tsx`
- Modify: `frontend/src/monitor/MonitorCenter.test.tsx`
- Modify: `frontend/src/monitor/monitor.css`

**Interfaces:**
- Produces: `filterRuns(runs, filters, now)` that returns only recent report execution history.
- Consumes: `MonitorSnapshot.updatedAt` as the front-end reference clock.

- [ ] **Step 1: Write failing data-service and component tests**

```ts
it("keeps only recent non-scheduled report executions", () => {
  const filtered = filterRuns(monitorRuns, EMPTY_FILTERS, new Date("2026-07-18T12:00:00+08:00"));
  expect(filtered.map((run) => run.id)).toEqual([
    "report-aijia-failed",
    "report-pksai-running",
    "report-pksai-success",
  ]);
});
```

```tsx
it("shows a 30-day report history and keeps the pending action beside status summaries", async () => {
  render(<MonitorCenter />);

  expect(await screen.findByText("自动通报 · 最近 30 天运行概览")).toBeTruthy();
  expect(screen.getByText((_, node) => node?.tagName === "P" && node.textContent === "最近 30 天 · 共 3 次")).toBeTruthy();
  expect(screen.queryByText("会话维护 · session-keeper")).toBeNull();
  const overview = screen.getByLabelText("运行概况");
  expect(within(overview).getByRole("button", { name: /待执行 48/ })).toBeTruthy();
});
```

Import `within` in the component test and retain the queue-drawer interaction assertion.

- [ ] **Step 2: Run the focused front-end tests and verify they fail**

Run: `npm run test -- mockMonitorService.test.ts MonitorCenter.test.tsx`

Expected: FAIL because the current service keeps all seven seed runs, the page says “最近 7 天”, and the pending button is in the filter bar.

- [ ] **Step 3: Implement the shared client-side history filter and layout move**

```ts
export const REPORT_HISTORY_DAYS = 30;

function isRecentReportExecution(run: MonitorRun, now: Date) {
  const reference = run.startedAt ?? run.scheduledAt;
  const cutoff = now.getTime() - REPORT_HISTORY_DAYS * 24 * 60 * 60 * 1000;
  return run.target.startsWith("通报 · ")
    && run.status !== "scheduled"
    && Boolean(reference)
    && new Date(reference).getTime() >= cutoff;
}

export function filterRuns(runs: MonitorRun[], filters: MonitorFilters, now = new Date()): MonitorRun[] {
  return runs.filter((run) => {
    if (!isRecentReportExecution(run, now)) return false;
    if (filters.target !== "all" && run.targetId !== filters.target) return false;
    if (filters.trigger !== "all" && run.trigger !== filters.trigger) return false;
    if (filters.status !== "all" && run.status !== filters.status) return false;
    const reference = run.startedAt ?? run.scheduledAt ?? "";
    return (!filters.startAt || reference >= filters.startAt)
      && (!filters.endAt || reference <= filters.endAt);
  });
}
```

Call `filterRuns(runs, filters, new Date(updatedAt))` in `MonitorCenter`; derive target options from `filteredRuns` or the same report-history predicate. Change the header and unfiltered record caption to “最近 30 天”. Remove the Scheduled option from the history status selector. Remove `filteredPendingQueue` from the filter bar, and place its existing button as the fourth `record-overview` button after failed, passing the unmodified `pendingQueue` to `PendingQueueDrawer`. Update responsive CSS so four overview buttons retain stable width and wrap cleanly without overlap.

- [ ] **Step 4: Run focused front-end tests and verify they pass**

Run: `npm run test -- mockMonitorService.test.ts MonitorCenter.test.tsx`

Expected: PASS; table, timeline and summary all report three current report runs, no excluded job is visible, and the pending queue still opens from the record overview.

### Task 4: Verify the integrated behavior

**Files:**
- Verify only: the files modified in Tasks 1-3.

- [ ] **Step 1: Run all affected automated checks**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_monitor_router.py tests/test_monitor_api_schema.py tests/test_dashboard_v2_retention_service.py -q
npm run typecheck --prefix frontend
npm run build --prefix frontend
npm run test --prefix frontend -- mockMonitorService.test.ts MonitorCenter.test.tsx
git diff --check
```

Expected: every command exits with code 0; `git diff --check` has no output.

- [ ] **Step 2: Inspect the running page**

Run: `npm run dev -- --host 127.0.0.1 --port 5175`

Verify in a browser at `http://127.0.0.1:5175/monitor.html`: the default view says “最近 30 天”, only report executions appear in the timeline/table/status totals, and the pending queue opens from the row of success/running/failed buttons.
