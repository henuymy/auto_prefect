---
name: template-updater
description: Update a temporary template copy only after report-compare has passed. Use when Codex needs to write downloaded report sheets into a template after comparison and before screenshot/sending.
---

# Template Updater

## Workflow

Use this skill after `report-compare` has produced a compare result.

1. Read `report-compare/runtime/compare_result.json`.
2. If the result is `invalid`, stop and do not touch the template.
3. If the result is `same`, skip because there is nothing to update.
4. If the result is `changed`, copy the official template to `template-updater/runtime/templates`.
5. Write changed report sheets into the copied template.
6. Save a JSON update manifest for the next workflow step.

The official template is not overwritten by this skill. A later commit step should replace the official template only after notification succeeds.

## Commands

Run from the workspace root:

```powershell
python template-updater\scripts\update_template.py --config template-updater\config.json
```

If `template-updater/config.json` exists, the script can also run without arguments:

```powershell
python template-updater\scripts\update_template.py
```

## Configuration

- `compare_result_path`: result JSON produced by `report-compare`.
- `compare_config_path`: config JSON used by `report-compare`; used to resolve report/template paths.
- `source_report_path`: optional override for the downloaded report path.
- `template_path`: optional override for the official template path.
- `output_dir`: where the updated temporary template copy should be written.
- `manifest_path`: update result JSON output path.
- `write_sheets`: `changed` or `all_compared`.
- `copy_mode`: currently `all`, copying sheet content and formatting from the report into the template copy.
- `visible`: show Excel while updating.
