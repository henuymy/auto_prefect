---
name: template-commit
description: Commit a temporary template copy as the official template only after WeCom sending succeeds. Use when Codex needs to finalize a notification workflow by safely replacing the baseline template.
---

# Template Commit

## Workflow

Use this skill after `excel-sender-wx` has sent the notification and `template-updater` has produced a temporary template copy.

1. Read `template-updater/runtime/update_manifest.json`.
2. Read `excel-sender-wx/runtime/send_result.json`.
3. Refuse to commit if WeCom sending failed or was only a dry run.
4. Back up the current official template.
5. Replace the official template with the updated temporary template.
6. Save a commit manifest.

This skill is the final baseline update step. It should not run before notification succeeds.

## Commands

Run from the workspace root:

```powershell
python template-commit\scripts\commit_template.py --config template-commit\config.json
```

If `template-commit/config.json` exists, it can also run without arguments:

```powershell
python template-commit\scripts\commit_template.py
```

## Configuration

- `update_manifest_path`: manifest produced by `template-updater`.
- `send_result_path`: send result produced by `excel-sender-wx`.
- `backup_dir`: directory for official template backups.
- `manifest_path`: commit result JSON output path.
- `require_send_success`: require all send results to have `errcode == 0`.
- `allow_dry_run_commit`: allow committing after dry-run sends. Keep this `false` for real workflows.
- `overwrite`: replace the official template. Keep this `true`; otherwise the script only validates and backs up nothing.
