# Stage Session And Business Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate session health by stage while sending one WeCom notification only for a development business Run that ultimately fails.

**Architecture:** Keep the existing `prepare_session()` and auto-login command as the authenticated refresh executor. Add a stage-oriented broker compatibility layer that records independent stage snapshots and health after each successful prepare. Move alert ownership to the outer business Flows; Session Keeper remains a preventive task that only logs and re-raises failures.

**Tech Stack:** Python 3.11, Prefect 3, pytest, existing JSON runtime state and WeCom client.

## Global Constraints

- Never include cookies, tokens, credentials, or webhook URLs in state-derived messages.
- Auto-login is invoked only after the requested stage fails validation or a caller explicitly requests refresh.
- A business Run emits at most one alert using `development:{workload_type}:{workload_id}:{flow_run_id}`.
- Session Keeper does not send failure or recovery notifications.

---

### Task 1: Persist stage-oriented session health

**Files:**
- Create: `services/session_broker.py`
- Modify: `tasks/session_tasks.py`
- Test: `tests/test_session_broker.py`

- [ ] Write failing tests for a successful `city_ops` prepare creating only that stage snapshot and marking other previously known stages `unknown` after a browser refresh.
- [ ] Implement `StageSessionBroker.ensure()` around `prepare_session()`, with atomic stage snapshot and health-state writes.
- [ ] Route `prepare_session_task()` through the broker while preserving its return schema.
- [ ] Run `python -m pytest -p no:cacheprovider tests/test_session_broker.py -q`.

### Task 2: Make business-run alerts terminal and scoped

**Files:**
- Create: `services/business_run_alert_service.py`
- Modify: `flows/notify_single_flow.py`
- Modify: `flows/dashboard_metric_flow.py`
- Test: `tests/test_business_run_alert_service.py`
- Test: `tests/test_notify_flow_session.py`
- Test: `tests/test_dashboard_metric_flow_session.py`

- [ ] Write failing tests for environment gating, duplicate suppression by Flow Run ID, and an ordinary download or collection exception producing a business alert.
- [ ] Implement persistent per-run alert state and a redacted business alert payload.
- [ ] Wrap the outer auto-notify and dashboard flows so their final exception is reported once, after all in-flow recovery attempts are exhausted.
- [ ] Remove inner session/recovery notifications so they cannot create duplicate or misleading shared-session messages.
- [ ] Run the listed tests.

### Task 3: Turn Session Keeper into prevention only

**Files:**
- Modify: `flows/session_keeper_flow.py`
- Modify: `prefect.yaml`
- Create: `config/modules/session_keeper_report.json`
- Create: `config/modules/session_keeper_city.json`
- Test: `tests/test_session_keeper_flow.py`
- Test: `tests/test_development_environment_contract.py`

- [ ] Write failing tests showing Keeper failures and successful reuse do not send notifications.
- [ ] Remove Keeper notification paths; retain Prefect logs and exception propagation.
- [ ] Split the current all-stage deployment into frequent report and low-frequency city stage deployments.
- [ ] Run the listed tests plus targeted deployment-contract checks.

### Task 4: Final verification

**Files:**
- Test: all files above

- [ ] Run targeted session, alert, flow, and deployment-contract tests.
- [ ] Run `git diff --check`.
- [ ] Record any unrelated pre-existing worktree changes without reverting them.
