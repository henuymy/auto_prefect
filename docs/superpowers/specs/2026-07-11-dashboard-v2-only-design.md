# Dashboard V2 Only Design

> Historical design record. The V1 cleanup was completed on 2026-07-14; do not use this document as an operational runbook. Current operations use `dashboard_v2`, `alembic_dashboard_v2.ini`, and the V2-only instructions in `README.md` and `PROJECT_GUIDE.md`.

## Goal

Remove the dashboard V1 implementation and make dashboard runtime configuration, database access, migrations, scheduled tasks, scripts, and tests V2-only. The dashboard remains available through its existing V2 API and frontend.

## Scope

- Delete V1-only dashboard models, services, migrations, Alembic configuration, operational tools, and tests.
- Move the V2-required declarative base and collection-run persistence behind V2-named modules.
- Remove runtime dispatch on `schema_version`. Dashboard configuration must require version `2` and target the V2 database.
- Retain V2 models, migrations, API routes, collection pipeline, target-plan features, maintenance flow, and their tests.

## Module Boundaries

`models.dashboard_v2_base` becomes the sole SQLAlchemy base for dashboard tables. V2 models must not import `models.dashboard_base`.

`infrastructure.dashboard_v2_run_store` owns collection-run persistence used by the V2 trigger, readiness/status reporting, and V2 collection pipeline. No runtime module imports the former V1 run-store path.

The V2 trigger is responsible for reading dashboard configuration and rejecting a schema version other than `2` with a clear configuration error. Callers no longer select V1 or V2 execution paths.

## Deletions

Delete `migrations/dashboard/`, `alembic_dashboard.ini`, V1 database models, V1 collection and indicator services, V1 import/export/audit scripts, V1-only environment settings, and V1 tests. Remove V1-specific Prefect deployment names and task wrappers.

Delete only code that is unreachable after the V2-only task and routing flow. Shared MySQL connection infrastructure remains because V2 uses it.

## Configuration and Operations

`config/dashboard/session.json` and runtime examples describe V2 only. The supported database is `dashboard_v2`; scripts source the V2 MySQL environment file only. Documentation exposes the V2 Alembic command and no V1 migration command.

An invalid or missing schema version fails before a collection task starts. Status and readiness responses continue to report MySQL and V2 schema state without depending on V1 collection-run data.

## Testing

Tests must cover V2 configuration validation, V2 task dispatch, V2 run-status persistence, and retained V2 dashboard API behavior. V1 tests are deleted with their production modules. The full suite must collect with `PYTHONPATH=.` and pass after the deletion.

## Non-Goals

- Migrating data from an existing V1 database to V2.
- Changing V2 table schemas or dashboard API contracts.
- Removing the dashboard feature, frontend, or shared MySQL infrastructure.
