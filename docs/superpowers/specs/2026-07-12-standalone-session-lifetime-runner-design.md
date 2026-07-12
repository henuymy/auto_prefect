# Standalone Session Lifetime Runner Design

## Goal

Create one self-contained Python file that can run the session lifetime
experiments unattended on a Windows machine. The file must not import modules
from this repository. It may depend on Python, Microsoft Edge, `selenium`, and
`requests` being installed.

The existing experiment runner and existing experiment output are out of
scope and must not be modified or resumed automatically.

## Deliverable

The deliverable is a single file:

`session_lifetime_runner_standalone.py`

All runner behavior, login automation, browser lifecycle handling, Cookie
capture, session probes, state persistence, recovery, status reporting, and
background-process commands live in this file.

Runtime data is created beside the script under:

`session_lifetime_runtime/`

The runtime directory contains the active run directory, state, events, logs,
browser profiles, Cookie snapshots, process metadata, and the singleton lock.

## Embedded Configuration

The top of the file contains one clearly marked configuration block with:

- Login username and password.
- Login URL and application-entry selectors.
- Three stage probe definitions: `report_analysis`, `smart_ops`, and
  `city_ops`.
- Edge executable path and browser options.
- Probe interval: 15 minutes.
- Failed-probe retry interval: 3 minutes.
- Consecutive failure threshold: 2.
- Request and login timeouts.

Credentials are intentionally stored directly in the Python file at the
user's request. They must never be included in logs, state files, exception
messages, command output, Cookie snapshots intended for inspection, or Git.
The standalone file must be ignored by Git before credentials are populated.

## Experiment Cases

The runner executes the following cases sequentially:

1. `headless_retained_idle`
2. `headed_closed_idle`
3. `headed_retained_idle`
4. `headless_closed_heartbeat`

Each case receives an isolated Edge user-data directory and isolated Cookie
snapshot. The complete Cookie snapshot is stored only in that protected case
directory and is never printed. A case remains active indefinitely while its
session is valid.

At each fixed probe time, all enabled stages are checked. When a probe fails,
the runner waits 3 minutes and probes once more. Two consecutive failed probes
end the current case, record its final result, clean up its browser processes,
and start the next case. Any successful probe resets the consecutive failure
count to zero.

The heartbeat case performs its configured heartbeat independently of the
15-minute observation probes.

## Login and Browser Lifecycle

The standalone file contains the required Selenium login sequence and stage
navigation. Login produces one Cookie snapshot covering the three probe
stages. The runner records only redacted Cookie metadata for diagnostics.

Cases that retain the browser keep their isolated Edge process alive between
probes. Cases that close the browser persist the Cookie snapshot and close the
isolated Edge process after login. Browser cleanup is restricted to processes
whose command line references the case's own user-data directory.

The runner must never terminate unrelated Edge processes.

## Persistence and Recovery

State is written atomically through a temporary file and replacement.
Append-only events are written as JSON Lines and flushed after every record.

The active state records at least:

- Runner PID and process start time.
- Current case and phase.
- Case baseline and last successful probe.
- Consecutive failure count.
- Next scheduled action.
- Browser process status.
- Completed cases.
- Last error or graceful-stop reason.

On normal startup, the runner finds the most recent standalone run:

- If no resumable run exists, it creates a new timestamped run.
- If the previous runner PID is dead and the current case has usable state,
  it resumes that case without a new login when its Cookie snapshot exists
  and the required retained browser is alive.
- If the browser is required but no longer alive, or required state is
  incomplete, it records the interrupted case and starts that case again with
  a fresh login.
- It never imports or resumes runs created by the existing project runner.

A singleton lock prevents two standalone runners from managing the same
runtime directory. The lock records PID, process start time, and an owner
token. A lock is removable only when its recorded owner process is no longer
the same live process.

## Commands

The file supports:

```powershell
python session_lifetime_runner_standalone.py
python session_lifetime_runner_standalone.py --background
python session_lifetime_runner_standalone.py --status
python session_lifetime_runner_standalone.py --stop
```

Default execution runs in the foreground. `--background` starts a detached,
hidden Python process using the same interpreter and returns after verifying
that the child acquired the singleton lock. It must not open a persistent
console window.

`--status` reads process and state information without probing or changing the
session. It reports stale state explicitly when the recorded PID is dead.

`--stop` writes a stop request. The runner observes it during interruptible
waits, records a graceful stop, cleans up only experiment-owned browser
processes, releases the singleton lock, and exits. It does not force-kill the
runner by default.

## Logging and Failure Handling

Foreground and background modes write structured operational logs to the
active run directory. Console output remains concise. Secrets and complete
Cookie values are redacted.

Expected session invalidation follows the two-probe case transition rule.
Infrastructure errors such as DNS failure, VPN loss, connection timeout, or
temporarily unavailable probe endpoints do not count as authentication
failure. They are recorded separately and retried every 3 minutes without
performing a new login or switching cases. Infrastructure retries continue
until connectivity recovers or the operator stops the runner. A missing Edge
process in a retained-browser case is recorded as a browser failure and ends
that case after the same two-observation confirmation rule.

An unhandled exception sets state to `failed`, records a sanitized error,
cleans up experiment-owned browser processes, and exits nonzero. A later
launch may recover from the persisted state according to the recovery rules.

## Safety and Validation

The implementation must provide a local validation command that checks:

- Required third-party modules are importable.
- The configured Edge executable exists.
- Required configuration values are present.
- Runtime directories are writable.
- Probe definitions are structurally valid.
- Credentials are not emitted by formatting, status, or error paths.

Unit-testable scheduling, failure counting, locking, atomic state, recovery,
and redaction logic must be exercised without performing a real login. A real
login is an explicit manual acceptance test and must not run as part of the
automated test suite.
