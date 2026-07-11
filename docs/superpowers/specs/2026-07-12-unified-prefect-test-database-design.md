# Unified Prefect Test Database Design

## Goal

Run the merged project against one clean, isolated Prefect PostgreSQL test
database on `49.233.78.70`, without importing either legacy Prefect database.

## Runtime Configuration

`config/runtime.local.json` remains the local, Git-ignored configuration
source. Its `prefect.postgres.url` will point directly to the new
`prefect_test` database on `49.233.78.70:5432`. The dashboard MySQL settings
remain unchanged.

## Startup Behavior

The Prefect startup path will use `AUTO_NOTIFY_PREFECT_DATABASE_URL` directly.
It will no longer require a `/prefect` suffix or derive a `/prefect_dev` URL.
This applies consistently to the primary Prefect startup script and legacy
environment helpers so every supported entry point selects the configured
database.

`scripts/run.ps1` will continue to validate the configured PostgreSQL and
MySQL connections before starting services. On first use, Prefect initializes
its own schema in the empty `prefect_test` database, then the existing startup
flow creates the work pool when absent and synchronizes repository deployments.

## Data and Rollback

No data is copied from the legacy `prefect` or `prefect_dev` databases. Their
history is retained on the old server for reference and rollback. Reverting is
limited to restoring the previous local PostgreSQL URL and restarting the
stack.

## Verification

Tests will first assert that a PostgreSQL URL with an arbitrary database name
is accepted and exported unchanged. After implementation, the focused runtime
environment tests will pass. A runtime verification will confirm connection to
`prefect_test`, successful Prefect health checks, work-pool creation, and
deployment synchronization.
