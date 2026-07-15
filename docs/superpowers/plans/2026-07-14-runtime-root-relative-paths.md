# Runtime-Root Relative Paths Implementation Plan

> **For agentic workers:** Execute each task with test-first changes and verify before moving on.

**Goal:** Make runtime configuration paths relative to `runtime.root` and remove production uses of the `runtime/` logical prefix.

**Architecture:** `runtime.local.json` supplies one absolute root. A root-relative resolver validates and expands `session/...`, `config/...`, operational, module, and Flow paths. Project-owned paths remain project relative.

### Task 1: Add the root-relative resolver

- [ ] Add failing tests proving root-relative paths expand below `runtime.root` and legacy `runtime/...` inputs are rejected by the new resolver.
- [ ] Implement the resolver in `services/runtime_paths.py` using the existing allow-list.
- [ ] Run `tests/test_runtime_paths.py`.

### Task 2: Migrate configuration consumers

- [ ] Change the 29 JSON runtime values to root-relative paths.
- [ ] Route session, downloader, template, notification, dashboard, and Flow output fields through the new resolver.
- [ ] Add focused configuration and consumer tests.

### Task 3: Migrate runtime producers and presentation

- [ ] Replace code defaults and generated runtime values with root-relative paths.
- [ ] Preserve project-relative configuration and template paths.
- [ ] Update API/log display strings to root-relative form.

### Task 4: Remove legacy-prefix acceptance

- [ ] Reject `runtime/...` in production runtime-value APIs.
- [ ] Update documentation and remaining tests.
- [ ] Run the full test suite and `git diff --check`.
