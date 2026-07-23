# Metric Current Nonlocking Read Design

## Goal

Avoid turning the current-value lookup used to build a realtime snapshot plan
into a large row-lock acquisition, while preserving the existing atomic metric
write transaction.

## Decision

`_load_current_values` is a nonlocking projection query. It will
fetch only `node_id`, `indicator_id`, `metric_value`, and `stat_date`, which
are the fields used by `build_v2_sparse_snapshot_plan`. The surrounding
transaction, the `CollectionRunV2` row lock, snapshot inserts, current upserts,
and final run transition remain unchanged.

The collection MySQL named lock serializes value-changing collectors. Retention
maintenance may clear `collection_run_id` under a different maintenance lock,
but it does not alter any field used by the snapshot decision. Any future path
that changes current metric values must acquire the collection lock or this
assumption must be reviewed.

## Error Handling

MySQL `2006` and `2013` during the write transaction are retried at most once.
Before retrying, the collector verifies that it still owns the named lock and
disposes the operational connection pool. A fresh connection checks whether the
batch is already `SUCCESS`; that state means a commit acknowledgement was lost,
so the pipeline returns the persisted counts without replaying snapshot writes.

If the batch is still running, the transaction is retried after a fixed
0.5-second delay. A lost named lock or a second connection-loss error is not
retried.

## Validation

Tests verify that the lookup returns the same current-value mapping, emits no
`FOR UPDATE` clause for MySQL, and projects no unrelated columns. Pipeline
tests cover one reconnect retry, committed-result recovery, and the one-retry
limit for connection-loss errors.
