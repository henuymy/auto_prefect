# Final review fix report

- RED: `pytest -q tests/test_dashboard_v2_ui_contract.py` → `1 failed, 2 passed`; the new cumulative-only cache-key assertion found 0 matching call sites.
- GREEN: `pytest -q tests/test_dashboard_v2_ui_contract.py` → `3 passed in 0.11s`.
- Scope: both dashboard cache-key call sites now include `cumulativeAsOf` only for `dataTimeMode === "cumulative"`; realtime and realtime-accumulation use `null`.
