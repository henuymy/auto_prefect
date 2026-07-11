# Unified Prefect Test Database Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the merged project against the clean `prefect_test` PostgreSQL database on `49.233.78.70` without deriving a second database name.

**Architecture:** `config/runtime.local.json` remains the only active local source for the Prefect database URL. All Prefect environment helpers and startup code pass that URL directly into Prefect, so the configured database name is the database Prefect initializes and uses. The existing MySQL environment mapping remains unchanged.

**Tech Stack:** PowerShell, Python 3.13, pytest, Prefect 3.7, PostgreSQL 16-compatible connection URLs.

## Global Constraints

- Do not migrate data from the legacy `prefect` or `prefect_dev` databases.
- Preserve `config/runtime.local.json` as Git-ignored local configuration and do not write credentials to tracked files.
- Preserve the existing MySQL configuration and connection lifecycle.
- `prefect.postgres.url` may name any PostgreSQL database, including `prefect_test`.

---

### Task 1: Define Direct-Database Environment Behavior

**Files:**
- Modify: `tests/test_development_environment_contract.py`
- Modify: `scripts/lib/prefect_start.ps1`
- Modify: `scripts/lib/prefect_env_prod.ps1`
- Modify: `scripts/dev/env.ps1`

**Interfaces:**
- Consumes: `AUTO_NOTIFY_PREFECT_DATABASE_URL` exported by `scripts/lib/runtime_config.ps1`.
- Produces: `PREFECT_API_DATABASE_CONNECTION_URL` and `PREFECT_SERVER_DATABASE_CONNECTION_URL` equal to the configured URL.

- [ ] **Step 1: Write the failing tests**

Add a test that asserts the startup helpers contain no `/prefect_dev` derivation and that the direct source URL is assigned to Prefect's API and server connection variables. Update the temporary JSON fixture URL to use `/prefect_test`.

```python
def test_prefect_helpers_use_configured_database_url_directly():
    prefect_start = (ROOT / "scripts" / "lib" / "prefect_start.ps1").read_text(encoding="utf-8")
    prefect_env = (ROOT / "scripts" / "lib" / "prefect_env_prod.ps1").read_text(encoding="utf-8")
    dev_env = (ROOT / "scripts" / "dev" / "env.ps1").read_text(encoding="utf-8")

    for source in (prefect_start, prefect_env, dev_env):
        assert "/prefect_dev" not in source
        assert "AUTO_NOTIFY_PREFECT_DATABASE_URL" in source

    assert "$DatabaseUrl = $env:AUTO_NOTIFY_PREFECT_DATABASE_URL" in prefect_start
    assert "$DatabaseUrl = $SourceDatabaseUrl" in prefect_env
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m pytest tests/test_development_environment_contract.py::test_prefect_helpers_use_configured_database_url_directly -v`

Expected: FAIL because `scripts/lib/prefect_start.ps1` still derives `/prefect_dev`.

- [ ] **Step 3: Implement direct URL selection**

In `scripts/lib/prefect_start.ps1`, replace the fallback block with:

```powershell
elseif (-not $DatabaseUrl) {
    $DatabaseUrl = $env:AUTO_NOTIFY_PREFECT_DATABASE_URL
    if (-not $DatabaseUrl) {
        throw "Missing Prefect database URL. Configure AUTO_NOTIFY_PREFECT_DATABASE_URL."
    }
}

if (-not $UseSqliteDebug -and $DatabaseUrl -notlike "postgresql+asyncpg://*") {
    throw "Prefect database URL must use the postgresql+asyncpg scheme."
}
```

In `scripts/lib/prefect_env_prod.ps1`, assign `$DatabaseUrl = $SourceDatabaseUrl` and remove the `/prefect` suffix validation and replacement. In `scripts/dev/env.ps1`, assign `$env:AUTO_NOTIFY_PREFECT_DEV_DATABASE_URL = $prodUrl` without validating or replacing its database suffix. Update display text to describe the configured Prefect database rather than `prefect_dev`.

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `python -m pytest tests/test_development_environment_contract.py::test_prefect_helpers_use_configured_database_url_directly -v`

Expected: PASS.

- [ ] **Step 5: Run the complete environment contract test module**

Run: `python -m pytest tests/test_development_environment_contract.py -v`

Expected: PASS.

### Task 2: Switch the Local Runtime to the Test Database

**Files:**
- Modify: `config/runtime.local.json`
- Test: `scripts/run.ps1 -SkipWeb`

**Interfaces:**
- Consumes: the target PostgreSQL account and the existing `prefect_test` database at `49.233.78.70:5432`.
- Produces: a local `prefect.postgres.url` pointing directly to `/prefect_test`.

- [ ] **Step 1: Update the local URL without changing unrelated settings**

Set `prefect.postgres.url` to a `postgresql+asyncpg://` URL for the target host, port `5432`, database `prefect_test`, and the target PostgreSQL account. Keep `prefect.api_url`, all `dashboard.mysql` fields, and `runtime.work_pool` unchanged.

- [ ] **Step 2: Validate the local JSON structure without printing secrets**

Run:

```powershell
$config = Get-Content -Raw config/runtime.local.json | ConvertFrom-Json
$config.prefect.postgres.url -replace '://[^@/]+@', '://<redacted>@'
```

Expected: a URL ending in `/prefect_test` with credentials redacted.

- [ ] **Step 3: Start and verify the isolated Prefect runtime**

Run: `pwsh -NoProfile -File scripts/run.ps1 -SkipWeb`

Expected: PostgreSQL and MySQL checks succeed; Prefect initializes its schema in `prefect_test`; the work pool is available; deployments synchronize; the Worker starts.

- [ ] **Step 4: Verify the Prefect health endpoint**

Run: `Invoke-WebRequest http://127.0.0.1:4200/api/health -UseBasicParsing`

Expected: HTTP 200.

### Task 3: Document the Direct-Database Runtime Contract

**Files:**
- Modify: `README.md`
- Modify: `scripts/environment.local.example.ps1`
- Test: `tests/test_development_environment_contract.py`

**Interfaces:**
- Consumes: the direct URL behavior established in Task 1.
- Produces: configuration examples and documentation consistent with arbitrary direct PostgreSQL database names.

- [ ] **Step 1: Write the failing documentation contract assertion**

Add assertions that the environment example does not describe `/prefect` to `/prefect_dev` derivation and that README documents `prefect.postgres.url` as the active PostgreSQL database URL.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m pytest tests/test_development_environment_contract.py -k direct_database -v`

Expected: FAIL until the example and README are updated.

- [ ] **Step 3: Update examples and documentation**

Replace the derived-database comments and placeholder URL in `scripts/environment.local.example.ps1` with a direct `prefect_test`-style placeholder. Update README so it states that the configured URL is passed directly to Prefect and may be changed from the test database to the later clean production database.

- [ ] **Step 4: Run the environment contract module again**

Run: `python -m pytest tests/test_development_environment_contract.py -v`

Expected: PASS.

- [ ] **Step 5: Review the patch before committing**

Run: `git diff --check` and `git diff -- tests/test_development_environment_contract.py scripts/lib/prefect_start.ps1 scripts/lib/prefect_env_prod.ps1 scripts/dev/env.ps1 README.md scripts/environment.local.example.ps1`

Expected: no whitespace errors, no credentials in tracked files, and no remaining `/prefect_dev` derivation in active scripts.
