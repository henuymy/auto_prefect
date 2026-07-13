# Probe Redirect Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Treat probe redirects as authentication failures without changing other probe failure classifications.

**Architecture:** Extend the existing `classify_probe_result` status-code classification before URL and payload inspection. Cover the changed behavior with direct unit tests.

**Tech Stack:** Python, pytest

## Global Constraints

- Do not change credential storage or login configuration.
- Preserve infrastructure classification for HTTP 500 responses.

---

### Task 1: Redirect Classification

**Files:**
- Modify: `session_lifetime_runner.py`
- Test: `tests/test_session_lifetime_runner.py`

**Interfaces:**
- Consumes: `classify_probe_result(result: dict) -> str`
- Produces: authentication classification for HTTP 300-399

- [ ] **Step 1: Write the failing tests**

Add direct assertions that status 302 returns `authentication_failure` and status 500 returns `infrastructure_failure`.

- [ ] **Step 2: Run tests to verify the redirect test fails**

Run: `python -m pytest tests/test_session_lifetime_runner.py -q`

Expected: the 302 assertion fails because the current result is `infrastructure_failure`.

- [ ] **Step 3: Write the minimal implementation**

Change the status check to classify `300 <= status_code < 400`, 401, and 403 as authentication failures.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_lifetime_runner.py -q`

Expected: all tests pass.

- [ ] **Step 5: Run the broader relevant suite**

Run: `python -m pytest tests/test_session_lifetime_runner.py tests/test_session_manager.py -q`

Expected: all tests pass.
