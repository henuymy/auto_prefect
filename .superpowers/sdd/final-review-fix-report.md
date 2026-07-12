# Final Review Fix Report

Date: 2026-07-12
Worktree: `C:\Users\yuyu\Desktop\项目\自动通报\.worktrees\session-keeper`

## Status

All six Important final-review findings were resolved without conflict with the approved design. The session-lifetime experiment runner and background experiment were not modified.

## Finding Mapping

1. Atomic post-validation publication
   - Login subprocesses now receive an attempt-specific private Cookie dump path through `AUTO_NOTIFY_COOKIE_DUMP_PATH`.
   - `CookieRecorder` writes each progressive private snapshot through a same-directory temporary file and `os.replace`.
   - Session Manager validates the private snapshot statically and with all configured probes, then publishes it to the shared path with `os.replace` only after success.
   - Failed attempts remove their private snapshot and leave the previous shared snapshot unchanged.
   - Regression coverage: atomic recorder write, shared snapshot visibility during probes, failed-attempt preservation.

2. `json_value_mismatch` authentication classification
   - Added `json_value_mismatch` to authentication failure reasons.
   - Regression coverage proves direct classification and that `prepare_session` enters the login path.

3. Lock envelope and timeout classification
   - Lock wait minimum is derived from two login timeouts, the fixed retry delay, and cleanup/probe overhead.
   - Bundled configuration uses 1440 seconds, exceeding the 1380-second derived envelope.
   - Login-lock acquisition timeouts are converted to `SessionInfrastructureError`; timeouts raised after lock acquisition retain their original meaning.
   - Regression coverage avoids real waiting and verifies both configuration rejection and exception classification.

4. Business-triggered failure alerts and deduplication
   - Added a shared business failure reporting service for `SessionLoginError` and `SessionInfrastructureError`.
   - Notify and Dashboard flows report their trigger source while using Keeper-compatible keys: `authentication:shared-session` and `infrastructure:shared-session`.
   - All paths reuse Session Manager policy and the shared incident notification state; no login policy was duplicated in flows.
   - Regression coverage includes shared incident construction, classified-only reporting, Notify wiring, and Dashboard wiring.

5. One Notify refresh budget per Flow run
   - Added a mutable `RefreshBudget` created once before the polling loop.
   - Every download polling iteration shares that budget; only the failed download operation is retried.
   - Initial `force_refresh=True` creates an already-consumed budget.
   - Integration regressions cover multiple polling iterations and initial forced mode.

6. Username and raw output safety
   - Login output now prints a fixed configured marker instead of the actual username.
   - Login command failures no longer include the command, stdout, or stderr.
   - `SessionLoginError` transports only bounded, field-sensitive sanitized summaries.
   - Alert redaction recognizes Chinese `用户名` plus stdout/stderr labels as defense in depth.
   - Regressions prove sentinel username and raw output secrets do not reach transported or sent alert text.

## TDD Evidence

- Initial focused RED: collection failed because `RefreshBudget` and `session_business_failure_service` did not exist.
- Notify integration RED: import failed because `run_notify_download_with_session_refresh` did not exist.
- Sanitized-summary RED: sentinel username and raw stdout remained in `SessionLoginError.errors` and exceeded the bound.
- GREEN after implementation:
  - `python -m pytest tests/test_session_manager.py -q` -> 37 passed.
  - Final focused command -> 114 passed.

## Final Verification

- `python -m pytest tests/test_cookie_recorder.py tests/test_session_manager.py tests/test_session_alert_service.py tests/test_session_business_failure_service.py tests/test_session_keeper_flow.py tests/test_session_retry_service.py tests/test_notify_flow_session.py tests/test_dashboard_metric_flow_session.py tests/test_dashboard_v2_trigger.py tests/test_development_environment_contract.py tests/test_notify_service.py tests/test_login_service.py -q`
  - Result: 114 passed in 12.55s.
- `python -m pytest -q`
  - Result: 520 passed, 14 skipped, 718 existing SQLAlchemy/Python 3.13 deprecation warnings in 34.62s.
- Scoped `python -m ruff check` over all changed Python files
  - Result: all checks passed.
- JSON parsing for `config/modules/autologin.json` and `config/modules/session_keeper.json`
  - Result: `json_ok`.
- YAML parsing for `prefect.yaml`
  - Result: `yaml_ok`.
- Production sentinel/placeholder scan over changed production/config files
  - Result: clean.
- `git diff --check`
  - Result: no whitespace errors; Git reported only expected LF-to-CRLF working-copy warnings.
- `git diff --name-only b70671e..HEAD --` experiment runner check
  - Result: no experiment or session-lifetime runner paths.

## Concerns

- The full suite continues to emit 718 pre-existing SQLAlchemy adapter deprecation warnings under Python 3.13. No test failures are associated with them.
- No live operational login or WeCom alert was executed because verification must not expose or exercise production credentials; behavior is covered by isolated regressions.

## Second Final Review Fix Wave

### Finding Mapping

1. Exhausted business authentication `RuntimeError` reporting
   - The shared business adapter now recognizes marker-bearing authentication `RuntimeError` values in addition to `SessionLoginError` and `SessionInfrastructureError`.
   - Notify reports the second authentication failure after a successful refresh, and reports authentication failures when the Flow refresh budget was already consumed by initial forced mode.
   - Dashboard reports the second authentication failure after its forced retry and reports initial `force_refresh=True` authentication failures without another retry.
   - The original exception object and traceback are preserved; no additional refresh is attempted.
   - All incidents use `authentication:shared-session` and the existing Flow-specific trigger source.

2. Business-originated shared recovery
   - Added shared recovery handling for both `authentication:shared-session` and `infrastructure:shared-session` through the existing locked `notify_session_recovery` service.
   - Successful Notify preparation and forced refresh invoke recovery.
   - Successful Dashboard completion invokes recovery after either direct reuse or the forced retry path.
   - Suppressed recovery remains a no-op when no matching incident is active.
   - A focused state regression proves failure -> business recovery -> later failure sends a new alert instead of remaining permanently suppressed.

3. Alert delivery must not replace the primary failure
   - Failure and recovery notification calls are isolated behind a safe adapter.
   - Notification exceptions are logged using only the notification exception type; their message is not logged.
   - The original classified or authentication-marked business exception is re-raised unchanged.

4. Integrated second-login-attempt coverage
   - Added focused `prepare_session` coverage where the first private snapshot fails static validation, the fixed 60-second retry substep is observed without sleeping, the second attempt uses a distinct private path, and only the second valid snapshot is atomically published.

### TDD Evidence

- RED: `python -m pytest tests/test_session_business_failure_service.py tests/test_notify_flow_session.py tests/test_dashboard_metric_flow_session.py -q`
  - Collection failed because `recover_business_session_incidents` did not exist.
- RED: `python -m pytest tests/test_notify_flow_session.py::test_notify_successful_preparation_runs_shared_recovery -q`
  - Collection failed because `run_notify_session_preparation` did not exist.
- GREEN after implementation:
  - Primary second-wave regressions: 18 passed.
  - Integrated second-login-attempt regression: 1 passed.
  - Final focused business/session/alert set: 90 passed in 9.15s.

### Final Verification

- `python -m pytest -q`
  - Result: 531 passed, 14 skipped, 718 existing SQLAlchemy/Python 3.13 deprecation warnings in 18.77s.
- Scoped `python -m ruff check` over all second-wave changed Python files
  - Result: all checks passed.
- JSON parsing for `config/modules/autologin.json` and `config/modules/session_keeper.json`
  - Result: `json_ok`.
- YAML parsing for `prefect.yaml`
  - Result: `yaml_ok`.
- Production secret scan over second-wave production files
  - Result: clean.
- `git diff --check`
  - Result: no whitespace errors; only expected LF-to-CRLF working-copy warnings.
- Experiment/session-lifetime runner path check against `b70671e..HEAD`
  - Result: unchanged.

### Remaining Concerns

- The full suite still emits 718 pre-existing SQLAlchemy adapter deprecation warnings under Python 3.13.
- Live credentialed login and WeCom delivery were not executed; isolated tests cover notification state, deduplication, recovery, and exception preservation.

## Invalid Preparation Recovery Guard

### Finding Mapping

- Notify session preparation now rejects `status=invalid` inside the operation passed to the shared business wrapper, preserving the existing `RuntimeError("会话不可用: <reason>")` behavior before recovery can run.
- Recovery eligibility is centralized through `notify_session_result_is_healthy` and is limited to explicit healthy Session Manager statuses: `reused`, `reused_after_lock`, and `refreshed`.
- Unknown/non-healthy statuses are returned without recovery and without being reclassified as failures.
- A stateful regression seeds an active authentication incident, returns `status=invalid`, and proves no recovery message is sent and the incident key remains active.

### TDD Evidence

- RED: `python -m pytest tests/test_notify_flow_session.py::test_notify_invalid_preparation_does_not_recover_or_clear_active_incident -q`
  - Failed because no `RuntimeError` was raised; the invalid result returned normally after recovery had run.
- GREEN: `python -m pytest tests/test_notify_flow_session.py tests/test_session_business_failure_service.py tests/test_session_alert_service.py tests/test_notify_service.py -q`
  - Result: 31 passed in 8.42s.

### Final Verification

- `python -m pytest -q`
  - Result: 533 passed, 14 skipped, 718 existing SQLAlchemy/Python 3.13 deprecation warnings in 19.05s.
- Scoped Ruff over the changed service, Notify Flow, and regression test
  - Result: all checks passed.
- Production secret scan
  - Result: clean.
- `git diff --check`
  - Result: no whitespace errors; only expected LF-to-CRLF working-copy warnings.
- Experiment/session-lifetime runner path check against `b70671e..HEAD`
  - Result: unchanged.

### Remaining Concerns

- The full suite still emits the same 718 pre-existing SQLAlchemy adapter deprecation warnings under Python 3.13.
- No live credentialed login or WeCom delivery was executed.
