# Prefect Monitor Target Identity Implementation Plan
> **归档状态：** 本文记录当时的需求、设计或实施计划；当前有效的运行契约以 [运行监控中心设计](../../run-monitoring-center-design.md) 和 [Webhook 运维手册](../../operations/prefect-monitor-webhook.md) 为准。
>


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Project Prefect deployment identity into the monitor page with a stable report target ID.

**Architecture:** Read flow runs through Prefect's REST API, batch-enrich them with deployments and flows, then persist normalized target metadata in the MySQL-owned monitor projection. The snapshot uses the persisted report kind for history and pending queues; the frontend receives the persisted target ID.

**Tech Stack:** Python 3.13, Prefect 3.7 REST API, FastAPI, SQLAlchemy, Alembic, MySQL, pytest, React, Vitest.

## Global Constraints

- Do not write project-owned tables to Prefect PostgreSQL or query its internal tables from application runtime code.
- A report is exactly `auto-notify-flow` with a deployment name beginning `notify-`.
- A report target ID is exactly `report-<Prefect deployment UUID>`.
- The monitor UI shows only report history and report Scheduled plans.
- Use `python -m pytest -p no:cacheprovider` for Python checks.

---

### Task 1: Normalize Prefect API data

**Files:**
- Modify: `backend/services/prefect_monitor_adapter.py`
- Modify: `backend/services/monitor_event_service.py`
- Test: `tests/test_prefect_monitor_adapter.py`

**Interfaces:**
- Consumes: raw Flow Run, Deployment, and Flow API payloads.
- Produces: `target_kind`, `target_id`, and normalized display `task_name` passed to `MonitorEventService.upsert_prefect_run`.

- [ ] **Step 1: Write failing report-identity tests**

```python
adapter.sync_flow_runs(
    [{"id": "run-1", "deployment_id": "dep-1", "state_type": "SCHEDULED", "state_name": "Scheduled"}],
    deployments_by_id={"dep-1": {"id": "dep-1", "name": "notify-经营日报", "flow_id": "flow-1"}},
    flows_by_id={"flow-1": {"id": "flow-1", "name": "auto-notify-flow"}},
)
stored = service.store.find_by_external_id("prefect", "run-1")
assert stored["target_kind"] == "report"
assert stored["target_id"] == "report-dep-1"
assert stored["task_name"] == "通报 · 经营日报"
```

- [ ] **Step 2: Run the test and verify it fails because the adapter uses the random Flow Run name.**

Run: `python -m pytest -p no:cacheprovider tests/test_prefect_monitor_adapter.py -q`

- [ ] **Step 3: Implement batch enrichment and normalization.**

`fetch_and_sync()` posts to `flow_runs/filter`, then posts the distinct `deployment_id` values to `deployments/filter`, then the resulting `flow_id` values to `flows/filter`. `sync_flow_runs()` classifies only the exact flow/deployment rule and calls `upsert_prefect_run(..., target_kind=..., target_id=...)`.

- [ ] **Step 4: Run the focused adapter test.**

Run: `python -m pytest -p no:cacheprovider tests/test_prefect_monitor_adapter.py -q`

### Task 2: Persist target identity in MySQL

**Files:**
- Modify: `models/monitor.py`
- Modify: `backend/services/mysql_monitor_store.py`
- Create: `migrations/dashboard_v2/versions/20260719_0003_monitor_target_identity.py`
- Test: `tests/test_monitor_event_service.py`

**Interfaces:**
- Produces: `MonitorRun.target_kind` and `MonitorRun.target_id` for API reads and re-sync upserts.

- [ ] **Step 1: Add a failing service assertion for a persisted Prefect target identity.**

```python
run = service.upsert_prefect_run(
    external_run_id="run-1", task_name="通报 · 经营日报", status="scheduled",
    target_kind="report", target_id="report-dep-1",
)
assert run["target_id"] == "report-dep-1"
```

- [ ] **Step 2: Run it and verify the current method rejects the new fields.**

Run: `python -m pytest -p no:cacheprovider tests/test_monitor_event_service.py -q`

- [ ] **Step 3: Add the fields to the service, in-memory store, SQLAlchemy model, and MySQL store.**

Use `VARCHAR(16)` for `target_kind`, `VARCHAR(48)` for `target_id`, and `KEY ix_monitor_runs_target_status (target_kind, status, scheduled_at)`. The migration populates `target_kind = 'legacy'` and `target_id = CONCAT('legacy-', id)` for existing rows before enforcing non-null columns.

- [ ] **Step 4: Run the focused service tests.**

Run: `python -m pytest -p no:cacheprovider tests/test_monitor_event_service.py -q`

### Task 3: Serve only persisted report identities

**Files:**
- Modify: `backend/services/monitor_api_schema.py`
- Modify: `backend/routers/monitor.py`
- Test: `tests/test_monitor_api_schema.py`
- Test: `tests/test_monitor_router.py`

**Interfaces:**
- Produces: frontend `targetId` from stored `target_id`, and report-only snapshot history and pending queue.

- [ ] **Step 1: Write failing API contract assertions.**

```python
report = {"id": "report", "target_kind": "report", "target_id": "report-dep-1", "task_name": "通报 · 日报", "status": "scheduled"}
system = {"id": "system", "target_kind": "system", "target_id": "prefect-dep-2", "task_name": "dashboard-collection", "status": "scheduled"}
assert to_frontend_run(report)["targetId"] == "report-dep-1"
assert [item["id"] for item in _snapshot_payload([report, system])["pendingQueue"]["items"]] == ["report"]
```

- [ ] **Step 2: Run the API tests and verify the pending queue still contains non-report rows.**

Run: `python -m pytest -p no:cacheprovider tests/test_monitor_api_schema.py tests/test_monitor_router.py -q`

- [ ] **Step 3: Implement identity-based serialization and filtering.**

`to_frontend_run()` returns `run["target_id"]`. `report_history_runs()` and pending-queue serialization filter on `target_kind == "report"`; no route predicate depends on a Chinese label.

- [ ] **Step 4: Run the focused API tests.**

Run: `python -m pytest -p no:cacheprovider tests/test_monitor_api_schema.py tests/test_monitor_router.py -q`

### Task 4: Keep the MySQL projection current

**Files:**
- Create: `backend/services/monitor_sync_service.py`
- Modify: `backend/app.py`
- Test: `tests/test_monitor_sync_service.py`

**Interfaces:**
- Produces: a bounded background loop that invokes the existing read-only Prefect adapter and stops cleanly at FastAPI shutdown.

- [ ] **Step 1: Write a failing loop test with an injected sync callable.**

```python
syncs = []
loop = MonitorSyncLoop(sync=lambda: syncs.append("sync"), interval_seconds=1)
loop.run_once()
assert syncs == ["sync"]
```

- [ ] **Step 2: Run it and verify the module is absent.**

Run: `python -m pytest -p no:cacheprovider tests/test_monitor_sync_service.py -q`

- [ ] **Step 3: Implement `sync_once()` and a stoppable 15-second thread loop, then start and stop it in the FastAPI lifespan.**

`sync_once()` creates `MySQLMonitorStore`, uses `PrefectMonitorAdapter(MonitorEventService(store=store)).fetch_and_sync()`, and always closes the store. The loop catches operational exceptions so monitor read endpoints remain available with the last successful projection.

- [ ] **Step 4: Run the sync service test.**

Run: `python -m pytest -p no:cacheprovider tests/test_monitor_sync_service.py -q`

### Task 5: Migrate, synchronize, and verify

**Files:**
- Verify: all files above

- [ ] **Step 1: Apply the MySQL migration and run one explicit sync.**

Run: `alembic -c migrations/dashboard_v2/alembic.ini upgrade head; python scripts/sync_monitor_prefect.py`

- [ ] **Step 2: Run automated verification.**

Run: `python -m pytest -p no:cacheprovider tests/test_prefect_monitor_adapter.py tests/test_monitor_event_service.py tests/test_monitor_api_schema.py tests/test_monitor_router.py tests/test_monitor_sync_service.py -q; npm run typecheck --prefix frontend; npm run build --prefix frontend; git diff --check`

- [ ] **Step 3: Start FastAPI and inspect live data.**

Verify `/api/monitor/snapshot` returns `targetId` values beginning `report-`, report-only `runs`, and report-only `pendingQueue`. Verify `monitor.html` displays the same count and excludes dashboard and session scheduled items.
