# Metric Current Nonlocking Read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the realtime current-value lookup a narrow, nonlocking read without changing metric write transaction semantics.

**Architecture:** The lookup remains inside the existing SQLAlchemy session and transaction. It returns lightweight rows containing only the values consumed by snapshot planning; `CollectionRunV2` locking and subsequent writes are untouched.

**Tech Stack:** Python 3, SQLAlchemy ORM, PyMySQL/MySQL, pytest.

## Global Constraints

- Do not change `CollectionRunV2` locking, named-lock behavior, scheduling, or timeout settings.
- Do not log SQL or parameters.
- Preserve the mapping key `(node_id, indicator_id)` and the `metric_value`/`stat_date` attributes consumed by sparse snapshot planning.

---

### Task 1: Narrow current-value lookup

**Files:**
- Modify: `services/dashboard_v2_metric_store.py:396-406`
- Modify: `tests/test_dashboard_v2_metric_store.py`

**Interfaces:**
- Consumes: `Session`, node IDs, and indicator IDs.
- Produces: `dict[tuple[int, int], row]` where each row exposes `node_id`, `indicator_id`, `metric_value`, and `stat_date`.

- [x] **Step 1: Write the failing tests**

```python
def test_current_value_lookup_is_narrow_and_nonlocking():
    result = dashboard_v2_metric_store._load_current_values(
        session, node_ids={1}, indicator_ids={100}
    )
    sql = str(session.statement.compile(dialect=mysql.dialect()))
    assert result == {(1, 100): rows[0]}
    assert "FOR UPDATE" not in sql
    assert list(session.statement.selected_columns.keys()) == [
        "node_id", "indicator_id", "metric_value", "stat_date",
    ]
```

- [x] **Step 2: Run the focused test to verify it fails**

Run: `pytest tests/test_dashboard_v2_metric_store.py -q`

Expected: the new assertion fails because the existing ORM query selects the full model and uses `FOR UPDATE`.

- [x] **Step 3: Implement the minimal query projection**

```python
select(
    MetricCurrentV2.node_id,
    MetricCurrentV2.indicator_id,
    MetricCurrentV2.metric_value,
    MetricCurrentV2.stat_date,
).where(
    MetricCurrentV2.node_id.in_(node_ids),
    MetricCurrentV2.indicator_id.in_(indicator_ids),
).order_by(MetricCurrentV2.node_id, MetricCurrentV2.indicator_id)
```

Do not call `.with_for_update()`.

- [x] **Step 4: Run focused and adjacent tests**

Run: `pytest tests/test_dashboard_v2_metric_store.py tests/test_dashboard_v2_pipeline.py -q`

Expected: all selected tests pass.

- [x] **Step 5: Run the full Python suite**

Run: `pytest -q`

Observed: `676 passed, 9 skipped, 4 failed`. The failures are in runtime
configuration encoding assertions and monitor health response expectations,
outside the modified code paths.

### Task 2: Recover a Single Lost Write-Transaction Connection

**Files:**
- Modify: `services/dashboard_v2_pipeline.py:35-258`
- Modify: `tests/test_dashboard_v2_pipeline.py`

**Interfaces:**
- Consumes: a `2006` or `2013` `OperationalError`, the batch engine, named-lock
  lease, and persisted `CollectionRunV2` state.
- Produces: one fresh transaction attempt, a recovered persisted success
  result, or the original exception.

- [x] **Step 1: Write failing retry tests**

```python
assert attempts == [1, 2]
database_lock.assert_held.assert_called_once_with()
engine.dispose.assert_called_once_with()
```

The committed-result case asserts the write callback is called once and the
persisted `SUCCESS` result is returned. The repeated-loss case asserts only two
attempts even when the configured transaction limit is higher.

- [x] **Step 2: Implement bounded connection-loss recovery**

```python
if error_code in {2006, 2013}:
    batch.database_lock.assert_held()
    batch.engine.dispose()
    completed = _completed_write_result(
        batch=batch,
        orchestrated=orchestrated,
        transaction_attempt=attempt,
    )
    if completed is not None:
        return completed
```

The helper reads only the current batch's `SUCCESS` record and rebuilds the
result from persisted counts. A second connection-loss error is raised.

- [x] **Step 3: Verify the pipeline tests**

Run: `pytest tests/test_dashboard_v2_pipeline.py -q`

Observed: `8 passed`.
