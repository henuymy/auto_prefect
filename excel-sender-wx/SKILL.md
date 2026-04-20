---
name: excel-sender-wx
description: Capture configured Excel workbook report sheets as images and send configured image/text items to WeCom. Use when Codex needs to screenshot Excel report pages, read notice text pages, process multiple .xlsx/.xlsm files, or send Excel report snapshots and text to WeCom group robots.
---

# Excel Sender WX

## Overview

Use this skill to process one or more Excel workbooks and send only the configured report items to WeCom. Each workbook defines its own reports, and each report defines ordered items.

## Workflow

1. Read the JSON config.
2. Process `workbooks[]` in order.
3. For each workbook, open Excel with links and refresh disabled by default.
4. Process `reports[]` in order.
5. Build a reusable message package JSON:
   - `type: "image"` captures the configured sheet/range, stores PNG metadata, base64, and md5.
   - `type: "text"` reads the configured sheet/range and stores text.
6. Build `runtime/preview.png` by stacking all captured images into one PNG for quick inspection.
7. Ignore every sheet that is not explicitly referenced by an item.
8. Close each workbook without saving.
9. Send the prepared package to WeCom in item order.

## Rules

- Do not infer report boundaries from sheet order.
- Do not auto-pair images and text.
- Send exactly what appears in `reports[].items[]`, in that order.
- Allow image-only and text-only reports.
- Default image capture mode is `used_range`.
- Use Excel COM on Windows so screenshots match real Excel formatting.
- Prefer `CopyPicture` appearance `printer` and format `picture` for clearer output.
- Use `export_scale` to increase the exported PNG pixel size when WeCom images look blurry.
- Use `optimize_png` with `png_colors` to reduce table image size after export.
- Keep `update_links` and `refresh_before_capture` false unless the user explicitly wants live refresh.
- Never save the workbook after capture.
- Keep capture/text extraction independent from WeCom sending.
- Retry sending from `runtime/message_package.json` without reopening Excel.
- Keep the image-only preview in `runtime/preview.png`; do not generate an HTML preview.
- Put common image capture settings in top-level `capture_defaults`; use item-level `capture` only for overrides.
- Put all generated files under top-level `output.runtime_dir`; keep other output names relative to that directory.
- Delete temporary package, preview, and image files after successful real sends when `output.cleanup_after_send` is true.

## Commands

Create or edit a config based on `references/config.example.json`, then run:

```powershell
python excel-sender-wx\scripts\main.py --config excel-sender-wx\config.json
```

Run the two steps separately when retrying or debugging:

```powershell
python excel-sender-wx\scripts\build_message_package.py --config excel-sender-wx\config.json
python excel-sender-wx\scripts\send_wecom_package.py --config excel-sender-wx\config.json
```

Test Excel capture and text extraction without sending to WeCom:

```powershell
python excel-sender-wx\scripts\build_message_package.py --config excel-sender-wx\config.json
```

Send a previously built package:

```powershell
python excel-sender-wx\scripts\send_wecom_package.py --config excel-sender-wx\config.json
```

Build a package and simulate sending:

```powershell
python excel-sender-wx\scripts\main.py --config excel-sender-wx\config.json --dry-run
```

## Config Shape

Use `workbooks[]` for multiple Excel files. Use `reports[]` to group the content in each workbook. Use `items[]` to control the actual send order.

```json
{
  "capture_defaults": {
    "mode": "used_range",
    "shrink_empty_edges": true,
    "appearance": "printer",
    "format": "picture",
    "export_scale": 2,
    "optimize_png": true,
    "png_colors": 256
  },
  "workbooks": [
    {
      "name": "default workbook",
      "file": "data/report.xlsx",
      "excel_open": {
        "update_links": false,
        "refresh_before_capture": false,
        "refresh_timeout_seconds": 120
      },
      "reports": [
        {
          "name": "default report",
          "items": [
            {
              "type": "image",
              "sheet": "notice"
            },
            {
              "type": "text",
              "sheet": "notice text",
              "text": {
                "mode": "used_range"
              }
            }
          ]
        }
      ]
    }
  ],
  "wecom": {
    "mode": "webhook",
    "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY"
  },
  "output": {
    "runtime_dir": "runtime",
    "image_dir": "images",
    "package_file": "message_package.json",
    "preview_image_file": "preview.png",
    "send_result_file": "send_result.json",
    "cleanup_after_send": true
  }
}
```

## Resources

- `scripts/build_message_package.py`: capture images and text into a reusable JSON package.
- `scripts/send_wecom_package.py`: send an existing message package to WeCom.
- `scripts/main.py`: run package building and sending in one command.
- `references/config.example.json`: reusable config example.
