# Simple Standalone Session Lifetime Runner Design

## Goal

Create one independent Python file whose only purpose is to measure how long
the service Session remains valid under four browser/session usage modes.

The program runs the experiments sequentially, requires no Codex supervision,
and records enough evidence to compare the Session lifetime of each mode.

## Scope

The deliverable is one local file:

`session_lifetime_runner.py`

It contains the login configuration, Selenium login steps, Cookie and Session
Storage capture, HTTP probes, experiment loop, and result writing. It does not
import modules from the current project.

The existing project experiment runner and all existing experiment output are
not modified or resumed.

## Experiment Cases

The runner executes exactly these cases in order:

1. `headless_retained_idle`: headless Edge remains open; no heartbeat.
2. `headed_closed_idle`: visible Edge closes after login; no heartbeat.
3. `headed_retained_idle`: visible Edge remains open; no heartbeat.
4. `headless_closed_heartbeat`: headless Edge closes after login; one
   heartbeat request runs every 5 minutes.

Only one case runs at a time. The next case starts only after the current case
has been confirmed invalid.

## Case Lifecycle

Each case performs this fixed sequence:

1. Create a new isolated Edge user-data directory for the case.
2. Log in once with the username and password embedded at the top of the file.
3. Open the required applications and capture Cookies and Session Storage for
   `report_analysis`, `smart_ops`, and `city_ops`.
4. Keep or close Edge according to the case definition.
5. Run an initial three-stage probe.
6. Probe all three stages every 15 minutes.
7. On the first authentication failure, wait 3 minutes and probe again.
8. If the retry is also an authentication failure, record the case lifetime,
   clean up its browser, and start the next case.
9. Any successful probe resets the authentication-failure count to zero.

A valid case continues indefinitely. There is no maximum case duration.

## Failure Classification

Authentication failure includes HTTP 401 or 403, login-page redirects, known
single-sign-on timeout responses, and application responses that explicitly
report an expired Session.

DNS, VPN, TLS, connection, and request-timeout failures are infrastructure
failures. They do not count as Session expiry and do not start the next case.
The runner waits 3 minutes and retries until connectivity returns.

For retained-browser cases, an unexpectedly exited owned Edge process is
recorded separately. It does not silently become evidence that the Session
expired.

## Lifetime Result

For each case, the runner records:

- Login completion time.
- Last successful probe time and elapsed seconds.
- First confirmed authentication-failure time and elapsed seconds.
- Retry authentication-failure time and elapsed seconds.
- Lower lifetime bound: elapsed time of the last successful probe.
- Upper lifetime bound: elapsed time of the first authentication failure.
- Browser mode and heartbeat mode.
- Per-stage status codes and sanitized failure reasons.

The Session lifetime is therefore reported as an interval rather than an
invented exact expiry time.

## Output

The script creates a timestamped directory beside itself:

```text
session_lifetime_results/<run timestamp>/
├── events.jsonl
├── results.json
├── runner.log
├── headless_retained_idle/
├── headed_closed_idle/
├── headed_retained_idle/
└── headless_closed_heartbeat/
```

`events.jsonl` contains append-only login, probe, heartbeat, cleanup, and case
completion events. `results.json` contains the current summary and is updated
atomically after every case transition.

Complete Cookie values, storage tokens, the password, and OTP values are never
written to events, results, or logs. The case directory may contain the private
Cookie snapshot required for probes.

## Running

Foreground execution is the only program mode:

```powershell
python session_lifetime_runner.py
```

Windows may launch the same command in the background with `Start-Process` and
redirect standard output and error to files. Background process management is
outside the Python program.

Stopping the Python process stops the experiment. A later launch creates a new
run and begins again from the first case. There is no internal background
launcher, stop command, status command, lock file, PID recovery, singleton
enforcement, automatic restart, or interrupted-run recovery.

## Dependencies and Security

The Windows machine must provide Python, Microsoft Edge, `selenium`, and
`requests`.

The username, password, and any configured OTP access values are stored
directly in the Python file at the user's request. The file and generated
results must be ignored by Git and kept only on the controlled Windows host.
Secrets and complete authentication values must not be printed.

## Validation

Pure logic for scheduling, two-failure confirmation, failure classification,
result bounds, atomic JSON writing, and redaction is tested without a real
login or production HTTP request.

The manual acceptance check runs one real login, verifies the initial
three-stage probe, confirms the expected browser-retention behavior, and then
stops the process. Full four-case execution is the experiment itself and is
not required before the file is considered ready.
