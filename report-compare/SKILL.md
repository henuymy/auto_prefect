---
name: report-compare
description: Compare a newly downloaded Excel report with the raw-data sheet in an official template. Use when Codex needs to decide whether downloaded report data changed before writing templates or sending notifications.
---

# Report Compare

## Workflow

Use this skill after `report-downloader` has produced a report file.

1. Choose the new report file.
2. Choose the official template file.
3. By default, compare every sheet in the new report with the same-named sheet in the template.
4. If names differ, configure `sheet_mappings`.
5. Run the compare script.
6. Use the result to decide whether the main workflow should continue.

## Commands

Run from the workspace root with a config file:

```powershell
python report-compare\scripts\compare_reports.py --config report-compare\config.json
```

Or pass paths directly:

```powershell
python report-compare\scripts\compare_reports.py `
  --new-report "report-downloader/runtime/downloads/xxx.xls" `
  --template "C:/path/to/template.xlsx" `
  --header-row 1
```

## Result

- `same`: all compared sheets are the same.
- `changed`: at least one sheet changed and no sheet is invalid.
- `invalid`: at least one sheet is empty, headers do not match, the template lacks a corresponding sheet, or key columns are invalid.

## Configuration

- `new_report_path`: downloaded report file.
- `template_path`: official template file.
- `sheet_mappings`: optional sheet map list. If empty, every sheet in the new report is matched by same name in the template.
- `skip_sheets`: report sheets to ignore.
- `new_range` / `template_range`: optional explicit Excel ranges inside each sheet mapping. If omitted, `UsedRange` is compared.
- `header_row`: header row number inside the compared range, starting from 1.
- `ignore_columns`: fields ignored during comparison.
- `key_columns`: fields used as a key. If omitted, rows are compared by order.
- `sample_limit`: maximum diff samples in the output.
- `output_path`: JSON result output path.

Keep file paths and sheet names in config, not in the script.

When report and template sheet names differ, configure mappings:

```json
{
  "sheet_mappings": [
    {
      "new_sheet_name": "下载报表页名",
      "template_sheet_name": "模板对应页名",
      "header_row": 1,
      "ignore_columns": [],
      "key_columns": []
    }
  ]
}
```
