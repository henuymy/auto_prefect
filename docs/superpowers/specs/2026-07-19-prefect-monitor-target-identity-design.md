# Prefect Monitor Target Identity Design
> **归档状态：** 本文记录当时的需求、设计或实施计划；当前有效的运行契约以 [运行监控中心设计](../../run-monitoring-center-design.md) 和 [Webhook 运维手册](../../operations/prefect-monitor-webhook.md) 为准。
>


## Goal

Carry the authoritative Prefect deployment identity to the monitor page so that report history, status summaries, and scheduled-report queues classify real runs consistently.

## Evidence

The Prefect PostgreSQL metadata shows that report runs use flow `auto-notify-flow` and deployments named `notify-*`. A Prefect flow-run has a stable `deployment_id`, but the official `flow_runs/filter` response does not embed deployment or flow data. Its generated run name is not a business identifier.

## Design

The runtime continues to read Prefect through its supported HTTP API and never writes application data to Prefect PostgreSQL. The adapter fetches a bounded set of flow runs, then batches `deployments/filter` and `flows/filter` lookups. It classifies a run as a report only when the flow is `auto-notify-flow` and the deployment name starts with `notify-`.

For a report, the adapter persists `target_kind = report`, `target_id = report-<deployment_id>`, and display name `通报 · <deployment name without notify->`. Other Prefect and web runs receive non-report target identities. `monitor_runs` owns these fields in MySQL, and monitor API serialization returns the stored `target_id` rather than deriving identity from display text.

The monitor snapshot filters both history and the scheduled queue by `target_kind = report`. The frontend retains its `report-` check as a defensive invariant only. A background sync loop in the FastAPI lifespan refreshes the MySQL projection from Prefect at a bounded interval; it performs no Prefect writes and never starts, retries, cancels, or changes deployments.

## Migration And Compatibility

An Alembic migration adds nullable target columns, backfills conservative non-report defaults for old rows, then makes the application write complete identities for every new or re-synced run. A sync immediately after deployment updates existing Prefect rows by their existing unique `(source, external_run_id)` key.

## Verification

Tests cover API response enrichment, deployment-ID report mapping, MySQL persistence, report-only snapshot behavior, and frontend pending-queue consumption. Live verification uses the running Prefect API, FastAPI monitor endpoints, and the monitor page.
