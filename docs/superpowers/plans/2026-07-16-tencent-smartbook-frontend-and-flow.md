# Tencent Smartbook Frontend and Flow Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `tencent_smartbook` download source that can be configured in React and used by test runs, starter-template generation, and notification flows.

**Architecture:** A new Python service calls the Tencent Smartbook subtable, field, and paginated-record APIs and writes selected subtables to one XLSX file. Existing configuration, validation, dispatch, and UI source-selection layers gain one third source without changing ordinary HTTP or Tencent Sheet behavior.

**Tech Stack:** Python 3.13, requests, openpyxl, pytest, React, TypeScript, Ajv, Vite.

## Global Constraints

- The source name is exactly `tencent_smartbook`.
- It shares `name`, `doc_url`, optional `file_id`, `output_filename`, and `sheets` with `tencent_sheet`.
- Each Smartbook subtable requires `sheet_id` or `sheet_name`, exports all fields and all record pages, and has no A1 `range`.
- Records with empty `values` are excluded from workbook rows but included in `fetched_records`.
- Credentials remain exclusively in ignored `config/modules/tencent_docs.local.json`.
- Normal `http_api` and `tencent_sheet` behavior must remain unchanged.

---

## File Structure

- Create: `services/tencent_smartbook_service.py` — Smartbook API client and XLSX exporter.
- Create: `tests/test_tencent_smartbook_service.py` — API, pagination, empty-record, and workbook tests.
- Modify: `services/method_service.py`, `flows/notify_single_flow.py`, `backend/services/config_store.py`, `backend/services/starter_template.py`, `backend/services/prefect_runner.py` — configuration and execution integration.
- Modify: `frontend/src/types/config.ts`, `frontend/src/schemas/reportConfigSchema.ts`, `frontend/src/components/config-form/ConfigForm.tsx` — third source tab and schema.
- Modify: `scripts/dev/test_tencent_smartbook_export.py`, `README.md`, `PROJECT_GUIDE.md` — shared diagnostic wrapper and documentation.

### Task 1: Implement the Smartbook exporter

**Files:**
- Create: `services/tencent_smartbook_service.py`
- Create: `tests/test_tencent_smartbook_service.py`

**Interfaces:**
- Consumes: `download_tencent_smartbook_report(report, output_dir, base_dir=PROJECT_DIR, session=None)`.
- Produces: a manifest item containing `source`, `file_id`, `output_path`, `bytes`, and per-subtable `fetched_records` and `records`.

- [ ] **Step 1: Write failing exporter tests**

```python
def test_download_tencent_smartbook_report_writes_selected_subtable_and_skips_empty_records():
    result = download_tencent_smartbook_report(
        {
            "source": "tencent_smartbook",
            "name": "智能日报",
            "doc_url": "https://docs.qq.com/smartsheet/Dexample?tab=sheet-1",
            "tencent_config_path": str(config_path),
            "sheets": [{"sheet_id": "sheet-1", "output_sheet_name": "汇总"}],
        },
        output_dir,
        session=FakeSmartbookSession(),
    )

    assert result["source"] == "tencent_smartbook"
    assert result["sheets"] == [{"sheet_id": "sheet-1", "output_sheet_name": "汇总", "fetched_records": 2, "records": 1}]
    assert load_workbook(result["output_path"], read_only=True)["汇总"]["A2"].value == "有效记录"


def test_download_tencent_smartbook_report_pages_records_in_config_order():
    result = download_tencent_smartbook_report(report_with_two_subtables, output_dir, session=PagedFakeSession())

    assert [sheet["sheet_id"] for sheet in result["sheets"]] == ["sheet-b", "sheet-a"]
    assert result["sheets"][0]["fetched_records"] == 2
```

- [ ] **Step 2: Run the tests and confirm the missing module failure**

Run: `python -m pytest tests/test_tencent_smartbook_service.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'services.tencent_smartbook_service'`.

- [ ] **Step 3: Write the minimal service**

```python
def download_tencent_smartbook_report(report, output_dir, base_dir=PROJECT_DIR, session=None):
    config = load_tencent_docs_config(report.get("tencent_config_path"), base_dir=base_dir)
    client = SmartbookClient(config, session=session)
    file_id = client.resolve_file_id(report)
    return write_smartbook_workbook(client, file_id, report, output_dir, base_dir=base_dir)
```

Implement `SmartbookClient.list_sheets()`, `list_fields()`, and `list_records()` using `/openapi/smartbook/v2/files/{fileID}/sheets`. POST `{"getFields": {"offset": offset, "limit": limit}}` and `{"getRecords": {"offset": offset, "limit": limit}}`; preserve the existing Tencent credential, retry, safe-filename, and error-redaction conventions. Match subtables by ID first, then name; reject repeated resolved IDs; stringify rich values; write a header plus non-empty record values.

- [ ] **Step 4: Verify the exporter and existing Sheet behavior**

Run: `python -m pytest tests/test_tencent_smartbook_service.py tests/test_tencent_sheet_service.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the exporter slice**

```text
git add services/tencent_smartbook_service.py tests/test_tencent_smartbook_service.py
git commit -m "feat: export Tencent smartbook data"
```

### Task 2: Integrate execution and configuration contracts

**Files:**
- Modify: `services/method_service.py`
- Modify: `flows/notify_single_flow.py`
- Modify: `backend/services/config_store.py`
- Modify: `backend/services/starter_template.py`
- Modify: `backend/services/prefect_runner.py`
- Modify: `tests/test_method_service.py`, `tests/test_flow_helpers.py`, `tests/test_config_store.py`, `tests/test_starter_template.py`, `tests/test_prefect_runner.py`

**Interfaces:**
- Consumes: `downloads[]` entries with `source: "tencent_smartbook"`.
- Produces: normalized, valid report configurations and manifest results without StageSessionBroker data.

- [ ] **Step 1: Write failing integration tests**

```python
def test_build_download_config_accepts_tencent_smartbook_without_stage():
    config = build_download_config(
        {"report_defaults": {}},
        {"downloads": [{"source": "tencent_smartbook", "name": "智能表", "doc_url": "https://docs.qq.com/smartsheet/Dexample?tab=sheet-1", "sheets": [{"sheet_id": "sheet-1"}]}]},
    )

    assert config["reports"][0]["source"] == "tencent_smartbook"
    assert "stage" not in config["reports"][0]


def test_download_reports_dispatches_tencent_smartbook_without_cookie_dump(monkeypatch):
    smartbook_report = {
        "source": "tencent_smartbook",
        "name": "智能表",
        "file_id": "file-1",
        "sheets": [{"sheet_id": "sheet-1"}],
    }
    monkeypatch.setattr("services.method_service.download_tencent_smartbook_report", fake_download)

    manifest = download_reports({"reports": [smartbook_report], "output_dir": str(output_dir), "manifest_path": str(manifest_path)})

    assert manifest["results"][0]["source"] == "tencent_smartbook"
```

Also add tests that reject missing document references or subtable selectors and assert starter-template/Prefect-runner Smartbook items do not request a Cookie Stage.

- [ ] **Step 2: Run the focused tests and confirm rejection**

Run: `python -m pytest tests/test_method_service.py tests/test_flow_helpers.py tests/test_config_store.py tests/test_starter_template.py tests/test_prefect_runner.py -k smartbook -v`

Expected: failures show unsupported source or missing Stage requirements.

- [ ] **Step 3: Add explicit Smartbook dispatch and validation branches**

```python
TENCENT_DOCUMENT_SOURCES = {"tencent_sheet", "tencent_smartbook"}

if report.get("source") == "tencent_smartbook":
    results.append(download_tencent_smartbook_report(report, output_dir, base_dir=base_dir))
    continue
```

Use `source in TENCENT_DOCUMENT_SOURCES` for session bypasses. Smartbook normalization creates a selector without `range`; validation requires `name`, `doc_url` or `file_id`, a non-empty `sheets` array, and a selector in each entry. Keep ordinary Sheet range defaults and validation unchanged.

- [ ] **Step 4: Verify the integration contract suite**

Run: `python -m pytest tests/test_method_service.py tests/test_flow_helpers.py tests/test_config_store.py tests/test_starter_template.py tests/test_prefect_runner.py -v`

Expected: all focused tests pass.

- [ ] **Step 5: Commit the contract slice**

```text
git add services/method_service.py flows/notify_single_flow.py backend/services/config_store.py backend/services/starter_template.py backend/services/prefect_runner.py tests/test_method_service.py tests/test_flow_helpers.py tests/test_config_store.py tests/test_starter_template.py tests/test_prefect_runner.py
git commit -m "feat: run Smartbook downloads in reports"
```

### Task 3: Add the third frontend data-source tab

**Files:**
- Modify: `frontend/src/types/config.ts`
- Modify: `frontend/src/schemas/reportConfigSchema.ts`
- Modify: `frontend/src/components/config-form/ConfigForm.tsx`

**Interfaces:**
- Consumes: UI edits to `DownloadItem`.
- Produces: valid Smartbook JSON with subtable selectors and no `range` fields.

- [ ] **Step 1: Write a failing type contract**

```ts
const smartbookDownload: DownloadItem = {
  source: "tencent_smartbook",
  name: "智能表格日报",
  doc_url: "https://docs.qq.com/smartsheet/Dexample?tab=sheet-1",
  output_filename: "智能表格日报.xlsx",
  headers: {},
  sheets: [{ sheet_id: "sheet-1", sheet_name: "汇总", output_sheet_name: "汇总" }],
};
```

- [ ] **Step 2: Run the type check and confirm `tencent_smartbook` is rejected**

Run: `npm run typecheck --prefix frontend`

Expected: TypeScript reports that `"tencent_smartbook"` is not assignable to the source union.

- [ ] **Step 3: Implement the model, schema, and UI card**

Add `TencentSmartbookConfig` without `range`; extend the source union and Ajv condition with the required document and selector fields. Add a third `activeSource` choice, count badge, empty state, add button, and `TencentSmartbookDownloadCard`. Reuse the ordinary Tencent card layout, `sheetIdFromDocUrl()` auto-fill, and subtable add/copy/remove controls; title the card “智能表格” and state that it exports all fields and records.

- [ ] **Step 4: Verify the frontend**

Run: `npm run typecheck --prefix frontend; npm run build --prefix frontend`

Expected: both commands exit 0.

- [ ] **Step 5: Commit the frontend slice**

```text
git add frontend/src/types/config.ts frontend/src/schemas/reportConfigSchema.ts frontend/src/components/config-form/ConfigForm.tsx
git commit -m "feat: configure Tencent smartbook downloads"
```

### Task 4: Convert the diagnostic script and document the feature

**Files:**
- Modify: `scripts/dev/test_tencent_smartbook_export.py`
- Modify: `README.md`
- Modify: `PROJECT_GUIDE.md`

**Interfaces:**
- Consumes: `--doc-url` or `--file-id`, repeated `--sheet id:<id>|name:<name>`, and `--output`.
- Produces: a service-generated XLSX and a redacted summary with `fetched_records` and `records`.

- [ ] **Step 1: Write a failing probe delegation test**

```python
def test_cli_uses_production_smartbook_downloader(monkeypatch, tmp_path):
    received = {}

    def fake_download(report, output_dir, base_dir, session=None):
        received.update(report)
        return {"source": "tencent_smartbook", "output_path": str(tmp_path / "output.xlsx"), "sheets": []}

    monkeypatch.setattr(probe, "download_tencent_smartbook_report", fake_download)

    result = probe.main(["--file-id", "file-1", "--sheet", "id:sheet-1", "--output", str(tmp_path / "output.xlsx")])

    assert result == 0
    assert received["source"] == "tencent_smartbook"
```

- [ ] **Step 2: Run the test and confirm the old probe does not delegate**

Run: `python -m pytest tests/test_tencent_smartbook_service.py -k cli -v`

Expected: failure because the probe has duplicate exporter logic.

- [ ] **Step 3: Replace probe duplication and update docs**

Make the probe build the production report dictionary and call `download_tencent_smartbook_report`; retain `--self-test`, selector parsing, and redacted output. Update README with Smartbook UI/configuration instructions and PROJECT_GUIDE with the implementation, credential boundary, verification, and rollback record.

- [ ] **Step 4: Run the final verification suite**

Run: `python -m pytest tests/test_tencent_smartbook_service.py tests/test_tencent_sheet_service.py tests/test_method_service.py tests/test_flow_helpers.py tests/test_config_store.py tests/test_starter_template.py tests/test_prefect_runner.py -v; python -m ruff check services backend flows scripts/dev tests; npm run typecheck --prefix frontend; npm run build --prefix frontend; git diff --check`

Expected: every command exits 0. If a permitted local Smartbook link and credentials are available, run the probe and inspect only sheet names, header count, and row counts.

- [ ] **Step 5: Commit the diagnostic and documentation slice**

```text
git add scripts/dev/test_tencent_smartbook_export.py README.md PROJECT_GUIDE.md
git commit -m "docs: document Tencent smartbook exports"
```
