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
