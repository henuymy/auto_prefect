import json
import shutil
import tempfile
from pathlib import Path

import requests
from openpyxl import load_workbook

from services.tencent_smartbook_service import download_tencent_smartbook_report


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
