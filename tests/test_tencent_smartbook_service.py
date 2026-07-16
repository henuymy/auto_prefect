import importlib.util
import json
import shutil
import tempfile
from pathlib import Path

import requests
from openpyxl import load_workbook

from services.tencent_smartbook_service import (
    SmartbookClient,
    download_tencent_smartbook_report,
    select_subtables,
    smart_value_to_text,
)


def make_work_dir():
    return Path(tempfile.mkdtemp(prefix="auto_notify_tencent_smartbook_test_"))


def make_response(payload):
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def write_tencent_config(work_dir):
    config_path = work_dir / "tencent_docs.json"
    write_json(
        config_path,
        {
            "credentials": {"client_id": "", "access_token": "", "open_id": ""},
            "request_timeout_seconds": 10,
            "retry": {"attempts": 1, "backoff_seconds": 0, "max_backoff_seconds": 0},
            "smartbook_read_limits": {"page_size": 1},
        },
    )
    write_json(
        work_dir / "tencent_docs.local.json",
        {"credentials": {"client_id": "cid", "access_token": "token", "open_id": "openid"}},
    )
    return config_path


class SingleSubtableSession:
    def get(self, url, **kwargs):
        if "util/converter" in url:
            assert kwargs["params"] == {"type": 2, "value": "Dexample"}
            return make_response({"ret": 0, "data": {"fileID": "file-1"}})
        if url.endswith("/openapi/smartbook/v2/files/file-1/sheets"):
            return make_response({"ret": 0, "data": {"sheets": [{"sheetID": "sheet-1", "title": "汇总"}]}})
        raise AssertionError(f"unexpected GET url: {url}")

    def post(self, url, **kwargs):
        assert url.endswith("/openapi/smartbook/v2/files/file-1/sheets/sheet-1")
        payload = kwargs["json"]
        if "getFields" in payload:
            assert payload == {"getFields": {"offset": 0, "limit": 1}}
            return make_response(
                {"ret": 0, "data": {"fields": [{"fieldID": "field-name", "title": "名称"}], "hasMore": False}}
            )
        if payload == {"getRecords": {"offset": 0, "limit": 1}}:
            return make_response(
                {
                    "ret": 0,
                    "data": {
                        "records": [
                            {"values": {"field-name": {"text": "有效记录"}}},
                        ],
                        "hasMore": True,
                    },
                }
            )
        if payload == {"getRecords": {"offset": 1, "limit": 1}}:
            return make_response({"ret": 0, "data": {"records": [{"values": {}}], "hasMore": False}})
        raise AssertionError(f"unexpected POST body: {payload}")


def test_download_tencent_smartbook_report_writes_selected_subtable_and_skips_empty_records():
    work_dir = make_work_dir()
    try:
        config_path = write_tencent_config(work_dir)

        result = download_tencent_smartbook_report(
            {
                "source": "tencent_smartbook",
                "name": "智能日报",
                "doc_url": "https://docs.qq.com/smartsheet/Dexample?tab=sheet-1",
                "tencent_config_path": str(config_path),
                "sheets": [{"sheet_id": "sheet-1", "output_sheet_name": "汇总"}],
            },
            work_dir / "downloads",
            session=SingleSubtableSession(),
        )

        workbook = load_workbook(result["output_path"], read_only=True, data_only=True)
        try:
            sheet = workbook["汇总"]
            assert sheet["A1"].value == "名称"
            assert sheet["A2"].value == "有效记录"
        finally:
            workbook.close()

        assert result["source"] == "tencent_smartbook"
        assert result["sheets"] == [
            {"sheet_id": "sheet-1", "output_sheet_name": "汇总", "fetched_records": 2, "records": 1}
        ]
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


class PagedSubtableSession:
    def get(self, url, **kwargs):
        if url.endswith("/openapi/smartbook/v2/files/file-1/sheets"):
            return make_response(
                {
                    "ret": 0,
                    "data": {
                        "sheets": [
                            {"sheetID": "sheet-a", "title": "甲"},
                            {"sheetID": "sheet-b", "title": "乙"},
                        ]
                    },
                }
            )
        raise AssertionError(f"unexpected GET url: {url}")

    def post(self, url, **kwargs):
        sheet_id = url.rsplit("/", 1)[-1]
        payload = kwargs["json"]
        if "getFields" in payload:
            return make_response(
                {"ret": 0, "data": {"fields": [{"fieldID": "title", "title": "标题"}], "hasMore": False}}
            )
        offset = payload["getRecords"]["offset"]
        if sheet_id == "sheet-b" and offset == 0:
            return make_response(
                {"ret": 0, "data": {"records": [{"values": {"title": {"text": "乙-1"}}}], "hasMore": True}}
            )
        if sheet_id == "sheet-b" and offset == 1:
            return make_response(
                {"ret": 0, "data": {"records": [{"values": {"title": {"text": "乙-2"}}}], "hasMore": False}}
            )
        if sheet_id == "sheet-a" and offset == 0:
            return make_response(
                {"ret": 0, "data": {"records": [{"values": {"title": {"text": "甲-1"}}}], "hasMore": False}}
            )
        raise AssertionError(f"unexpected POST body: {payload}")


def test_download_tencent_smartbook_report_pages_records_in_config_order():
    work_dir = make_work_dir()
    try:
        config_path = write_tencent_config(work_dir)

        result = download_tencent_smartbook_report(
            {
                "source": "tencent_smartbook",
                "name": "智能日报",
                "file_id": "file-1",
                "tencent_config_path": str(config_path),
                "sheets": [
                    {"sheet_name": "乙", "output_sheet_name": "乙"},
                    {"sheet_id": "sheet-a", "output_sheet_name": "甲"},
                ],
            },
            work_dir / "downloads",
            session=PagedSubtableSession(),
        )

        assert [sheet["sheet_id"] for sheet in result["sheets"]] == ["sheet-b", "sheet-a"]
        assert result["sheets"][0]["fetched_records"] == 2
        assert result["sheets"][0]["records"] == 2
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_smartbook_client_supports_get_operation_response_wrappers():
    class WrappedResponseSession:
        def get(self, url, **kwargs):
            assert url.endswith("/openapi/smartbook/v2/files/file-1/sheets")
            return make_response({"ret": 0, "data": {"getSheet": [{"sheetID": "sheet-1", "title": "汇总"}]}})

        def post(self, url, **kwargs):
            payload = kwargs["json"]
            if "getFields" in payload:
                return make_response(
                    {
                        "ret": 0,
                        "data": {
                            "getFields": {
                                "fields": [{"fieldID": "name", "fieldTitle": "名称"}],
                                "hasMore": False,
                            }
                        },
                    }
                )
            return make_response(
                {
                    "ret": 0,
                    "data": {
                        "getRecords": {
                            "records": [{"values": {"name": {"text": "有效记录"}}}],
                            "hasMore": False,
                        }
                    },
                }
            )

    client = SmartbookClient(
        {
            "credentials": {"client_id": "cid", "access_token": "token", "open_id": "openid"},
            "retry": {"attempts": 1, "backoff_seconds": 0, "max_backoff_seconds": 0},
        },
        {},
        session=WrappedResponseSession(),
    )

    assert client.list_sheets("file-1") == [{"sheetID": "sheet-1", "title": "汇总"}]
    assert client.list_fields("file-1", "sheet-1") == [{"fieldID": "name", "fieldTitle": "名称"}]
    assert client.list_records("file-1", "sheet-1") == [{"values": {"name": {"text": "有效记录"}}}]


def test_download_tencent_smartbook_report_reads_field_title_values_from_operation_wrappers():
    work_dir = make_work_dir()
    try:
        config_path = write_tencent_config(work_dir)

        class OperationWrappedSession:
            def get(self, url, **kwargs):
                assert url.endswith("/openapi/smartbook/v2/files/file-1/sheets")
                return make_response({"ret": 0, "data": {"getSheet": [{"sheetID": "sheet-1", "title": "汇总"}]}})

            def post(self, url, **kwargs):
                if "getFields" in kwargs["json"]:
                    return make_response(
                        {"ret": 0, "data": {"getFields": {"fields": [{"fieldTitle": "名称"}], "hasMore": False}}}
                    )
                return make_response(
                    {
                        "ret": 0,
                        "data": {
                            "getRecords": {
                                "records": [{"values": {"名称": {"text": "实际接口记录"}}}],
                                "hasMore": False,
                            }
                        },
                    }
                )

        result = download_tencent_smartbook_report(
            {
                "source": "tencent_smartbook",
                "name": "智能日报",
                "file_id": "file-1",
                "tencent_config_path": str(config_path),
                "sheets": [{"sheet_id": "sheet-1", "output_sheet_name": "汇总"}],
            },
            work_dir / "downloads",
            session=OperationWrappedSession(),
        )

        workbook = load_workbook(result["output_path"], read_only=True, data_only=True)
        try:
            assert workbook["汇总"]["A1"].value == "名称"
            assert workbook["汇总"]["A2"].value == "实际接口记录"
        finally:
            workbook.close()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_smartbook_client_uses_next_cursor_from_operation_response_wrappers():
    requested_offsets = []

    class CursorSession:
        def post(self, url, **kwargs):
            offset = kwargs["json"]["getRecords"]["offset"]
            requested_offsets.append(offset)
            if offset == 0:
                return make_response(
                    {
                        "ret": 0,
                        "data": {
                            "getRecords": {
                                "records": [{"values": {"名称": "第一页"}}],
                                "hasMore": True,
                                "next": 5,
                            }
                        },
                    }
                )
            assert offset == 5
            return make_response(
                {
                    "ret": 0,
                    "data": {
                        "getRecords": {
                            "records": [{"values": {"名称": "第二页"}}],
                            "hasMore": False,
                        }
                    },
                }
            )

    client = SmartbookClient(
        {
            "credentials": {"client_id": "cid", "access_token": "token", "open_id": "openid"},
            "retry": {"attempts": 1, "backoff_seconds": 0, "max_backoff_seconds": 0},
        },
        {},
        session=CursorSession(),
    )

    assert [record["values"]["名称"] for record in client.list_records("file-1", "sheet-1")] == ["第一页", "第二页"]
    assert requested_offsets == [0, 5]


def test_cli_uses_production_smartbook_downloader(monkeypatch, tmp_path):
    probe_path = Path(__file__).resolve().parents[1] / "scripts" / "dev" / "test_tencent_smartbook_export.py"
    spec = importlib.util.spec_from_file_location("tencent_smartbook_probe", probe_path)
    assert spec and spec.loader
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    received = {}

    def fake_download(report, output_dir, base_dir, session=None):
        received.update(report)
        return {
            "source": "tencent_smartbook",
            "output_path": str(tmp_path / "output.xlsx"),
            "sheets": [],
        }

    monkeypatch.setattr(probe, "download_tencent_smartbook_report", fake_download)

    result = probe.main([
        "--file-id", "file-1",
        "--sheet", "id:sheet-1",
        "--output", str(tmp_path / "output.xlsx"),
    ])

    assert result == 0
    assert received["source"] == "tencent_smartbook"
    assert received["file_id"] == "file-1"
    assert received["sheets"] == [{"sheet_id": "sheet-1"}]


def test_smart_value_to_text_uses_option_labels_for_multi_select_values():
    assert smart_value_to_text(
        {
            "value": ["first", "second"],
            "options": [
                {"id": "first", "text": "第一项"},
                {"id": "second", "text": "第二项"},
            ],
        }
    ) == "第一项, 第二项"


def test_select_subtables_rejects_range_for_smartbook():
    try:
        select_subtables(
            [{"sheetID": "sheet-1", "title": "汇总"}],
            [{"sheet_id": "sheet-1", "range": "A1:B2"}],
        )
    except ValueError as exc:
        assert "range" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_smartbook_client_rejects_empty_page_that_claims_more_records():
    class InvalidPaginationSession:
        def post(self, url, **kwargs):
            return make_response(
                {
                    "ret": 0,
                    "data": {"getRecords": {"records": [], "hasMore": True, "next": 1}},
                }
            )

    client = SmartbookClient(
        {
            "credentials": {"client_id": "cid", "access_token": "token", "open_id": "openid"},
            "retry": {"attempts": 1, "backoff_seconds": 0, "max_backoff_seconds": 0},
        },
        {},
        session=InvalidPaginationSession(),
    )

    try:
        client.list_records("file-1", "sheet-1")
    except RuntimeError as exc:
        assert "分页" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
