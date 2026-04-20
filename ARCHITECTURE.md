# Architecture

1. `flows` only orchestrate Prefect flows and branch decisions.
2. `tasks` only wrap service calls as Prefect tasks.
3. `services` contain business logic and should not depend on Prefect.
4. `infrastructure` contains low-level clients for HTTP, Excel, WeCom, Gotify, and storage.
5. Login/session reuse is owned by `services/session_manager.py`.
6. Compare results must be one of `same`, `changed`, or `invalid`.
7. Baseline templates can only be committed after notification succeeds.
8. Runtime outputs belong under `runtime/` and must not be committed.
