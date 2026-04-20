---
name: autologin
description: Automate NGBOSS/4A browser login with Gotify SMS dynamic-key retrieval and capture staged Cookie JSON from ngboss_main, USM, and configured USM applications. Use when working in this project to test login, refresh sessions, diagnose cross-domain cookies, or export staged Cookie JSON for NGBOSS/USM automation.
---

# Autologin

## Workflow

Use this skill for the NGBOSS login chain:

1. Establish a Gotify message baseline before the first login click.
2. Trigger NGBOSS/4A login and wait for a new SMS dynamic key from Gotify.
3. Submit the dynamic key and complete NGBOSS login.
4. Capture `ngboss_main`, `usm_console`, and configured USM app cookies during the same login run.
5. Save staged Cookie JSON and leave Edge open for inspection.

## Commands

Run commands from the skill directory unless a caller passes absolute paths:

```powershell
cd autologin
python scripts/autologin.py
```

Use a custom config:

```powershell
python scripts/autologin.py --config C:\path\to\config.json
```

## Configuration

Use `autologin/config.json` for real local credentials and tokens. Do not print or share it. Use `references/config.example.json` for the expected shape.

Important Gotify fields:

- `title_prefix`: actual Gotify message title, often the SMS sender such as `10658221`
- `allowed_senders`: sender/title strings expected in the message
- `required_keywords`: stable text near the dynamic key, configured in `references/config.example.json`
- `preferred_code_lengths`: usually `[6]`
- `require_new_message`: keep `true` so old dynamic keys are ignored
- `max_request_count`: resend SMS after timeout, up to this count
- `usm_cookie_apps`: USM app entries to click and capture, each with `stage` and visible `name`
- To add another USM page, append a `usm_cookie_apps` item with only `stage` and visible `name`; downstream page URLs are not configured or used for validation.
- `jsessionid_stage`: optional stage to read `JSESSIONID` from; omit it for normal generic Cookie capture.
- `cookie_dump.enabled`: keep `true` to write staged cookies during login

Navigation is strict: do not directly open downstream URLs to recover from a failed prior step. NGBOSS main, USM, and USM apps must be reached through the normal authenticated chain.

## Outputs

- Login saves `runtime/cookie_dump.json` under the skill directory by default.
- This output contains sensitive session material and must stay ignored by git.
