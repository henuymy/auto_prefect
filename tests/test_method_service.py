import json
import shutil
from pathlib import Path
from uuid import uuid4

import requests

from services.method_service import (
    build_request_kwargs,
    build_headers,
    build_cookie_string,
    download_reports,
    filename_from_content_disposition,
    find_stage,
    output_filename_for_report,
    raise_for_status_with_context,
)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_work_dir():
    work_dir = Path("runtime/test_work") / uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def test_find_stage_and_build_headers_from_cookies():
    cookie_dump = {
        "stages": [
            {
                "stage": "report_analysis",
                "cookies": [
                    {"name": "JSESSIONID", "value": "abc"},
                    {"name": "ssr-header", "value": "X-CSRF-TOKEN"},
                    {"name": "ssr-token", "value": "token-value"},
                ],
            }
        ]
    }
    report = {
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        "headers_from_cookies": {"ssr-token": "ssr-token"},
    }

    stage = find_stage(cookie_dump, "report_analysis")
    headers = build_headers(report, stage)

    assert headers["ssr-token"] == "token-value"


def test_build_headers_from_session_storage_json_path():
    stage = {
        "cookies": [],
        "session_storage": {
            "zhyyptInfo": json.dumps(
                {
                    "accessToken": "dynamic-user-info",
                    "areaId": "AQ",
                }
            )
        },
    }
    report = {
        "headers": {"Content-Type": "application/json"},
        "headers_from_session_storage": {"User-Info": "zhyyptInfo.accessToken"},
    }

    headers = build_headers(report, stage)

    assert headers["Content-Type"] == "application/json"
    assert headers["User-Info"] == "dynamic-user-info"


def test_build_headers_from_cookie_string_for_uap_token():
    stage = {
        "cookies": [
            {"name": "JSESSIONID", "value": "abc"},
            {"name": "locale_cookie", "value": "zh_CN"},
        ]
    }
    report = {
        "headers_from_cookie_string": {"uapToken": "*"},
    }

    headers = build_headers(report, stage)

    assert headers["uapToken"] == "JSESSIONID=abc; locale_cookie=zh_CN"
    assert build_cookie_string(stage, ["JSESSIONID"]) == "JSESSIONID=abc"


def test_build_headers_missing_session_storage_value_triggers_session_retry():
    stage = {
        "cookies": [],
        "session_storage": {
            "zhyyptInfo": json.dumps({"accessToken": ""})
        },
    }
    report = {
        "headers_from_session_storage": {"User-Info": "zhyyptInfo.accessToken"},
    }

    try:
        build_headers(report, stage)
    except RuntimeError as exc:
        assert "session 已过期" in str(exc)
        assert "User-Info" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_filename_from_content_disposition_utf8():
    filename = filename_from_content_disposition(
        "attachment; filename*=UTF-8''%E6%97%A5%E9%80%9A%E6%8A%A5.xls",
        "https://example/export",
    )

    assert filename == "日通报.xls"


def test_output_filename_for_report_prefixes_download_name():
    filename = output_filename_for_report({"name": "新增"}, "报表.xlsx")

    assert filename == "新增__报表.xlsx"


def test_output_filename_for_report_keeps_existing_prefix():
    filename = output_filename_for_report({"name": "新增"}, "新增__报表.xlsx")

    assert filename == "新增__报表.xlsx"


def test_build_request_kwargs_sends_json_body_type_as_json():
    stage = {"cookies": []}
    report = {
        "headers": {"Content-Type": "application/json"},
        "body_type": "json",
        "data": {"date": "${today}"},
    }

    kwargs = build_request_kwargs(report, stage, 30, False, None)

    assert kwargs["json"] == {"date": "${today}"}
    assert "data" not in kwargs


def test_build_request_kwargs_resolves_session_storage_tokens_in_body():
    stage = {
        "cookies": [],
        "session_storage": {
            "zhyyptInfo": json.dumps({"accessToken": "dynamic-user-info"})
        },
    }
    report = {
        "headers": {"User-Info": "${session_storage:zhyyptInfo.accessToken}"},
        "body_type": "json",
        "data": {"user_info": "${session_storage:zhyyptInfo.accessToken}"},
    }

    kwargs = build_request_kwargs(report, stage, 30, False, None)

    assert kwargs["headers"]["User-Info"] == "dynamic-user-info"
    assert kwargs["json"]["user_info"] == "dynamic-user-info"


class FakeRequest:
    method = "POST"


class FakeResponse:
    def __init__(self, status_code, text, url="https://example/export", headers=None):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url
        self.headers = {"Content-Type": "application/json"}
        if headers:
            self.headers.update(headers)
        self.request = FakeRequest()

    def raise_for_status(self):
        raise requests.HTTPError("failed")

    def json(self):
        return json.loads(self.text)


def test_raise_for_status_treats_401_json_login_as_session_expired():
    response = FakeResponse(
        401,
        '{"status":401,"error":"Unauthorized","message":"http://example/login.jsp"}',
    )

    try:
        raise_for_status_with_context(response)
    except RuntimeError as exc:
        assert "session 已过期" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_raise_for_status_treats_302_login_redirect_as_session_expired():
    response = FakeResponse(
        302,
        "",
        headers={"Location": "http://ngbossgq.ha.cmcc/uac/web3/jsp/login/login.jsp"},
    )

    try:
        raise_for_status_with_context(response)
    except RuntimeError as exc:
        assert "session 已过期" in str(exc)
        assert "location=" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_raise_for_status_reports_non_auth_redirect_location():
    response = FakeResponse(
        302,
        "",
        headers={"Location": "https://example.com/download/file.xlsx"},
    )

    try:
        raise_for_status_with_context(response)
    except RuntimeError as exc:
        assert "不是明确登录地址" in str(exc)
        assert "file.xlsx" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_build_request_kwargs_sends_raw_body_as_data():
    stage = {"cookies": []}
    report = {
        "headers": {"Content-Type": "text/plain"},
        "body_type": "raw",
        "raw_body": "raw payload",
    }

    kwargs = build_request_kwargs(report, stage, 30, False, None)

    assert kwargs["data"] == "raw payload"
    assert "json" not in kwargs


def test_build_request_kwargs_sends_form_body_type_as_data():
    stage = {"cookies": []}
    report = {"body_type": "form", "data": {"templateId": "85479"}}

    kwargs = build_request_kwargs(report, stage, 30, False, None)

    assert kwargs["data"] == {"templateId": "85479"}
    assert "json" not in kwargs


def test_download_reports_dry_run_writes_manifest():
    work_dir = make_work_dir()
    try:
        cookie_dump_path = work_dir / "cookie_dump.json"
        manifest_path = work_dir / "manifest.json"
        write_json(
            cookie_dump_path,
            {
                "stages": [
                    {
                        "stage": "report_analysis",
                        "cookies": [
                            {"name": "ssr-token", "value": "token"},
                        ],
                    }
                ]
            },
        )

        manifest = download_reports(
            {
                "cookie_dump_path": str(cookie_dump_path),
                "manifest_path": str(manifest_path),
                "reports": [
                    {
                        "enabled": True,
                        "name": "日报",
                        "stage": "report_analysis",
                        "method": "POST",
                        "url": "https://example/export",
                        "body_type": "form",
                        "response_mode": "file",
                    }
                ],
            },
            dry_run=True,
            debug=True,
        )

        assert manifest["dry_run"] is True
        assert manifest["results"][0]["request_summary"]["method"] == "POST"
        assert manifest_path.exists()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_download_reports_json_to_excel_writes_output(monkeypatch):
    work_dir = make_work_dir()
    try:
        cookie_dump_path = work_dir / "cookie_dump.json"
        manifest_path = work_dir / "manifest.json"
        output_dir = work_dir / "downloads"
        write_json(cookie_dump_path, {"stages": [{"stage": "city_ops", "cookies": []}]})

        class FakeSession:
            trust_env = False

            def __init__(self):
                self.cookies = requests.cookies.RequestsCookieJar()
                self.headers = {}

            def request(self, method, url, **kwargs):
                response = FakeResponse(
                    200,
                    json.dumps(
                        {
                            "reCode": "0000",
                            "result": {
                                "tableData": [
                                    {"areaName": "西流湖网格", "areaCode": "AQ726", "sgs_ajvwdz": "5"}
                                ]
                            },
                        },
                        ensure_ascii=False,
                    ),
                    url=url,
                )
                response.request.method = method
                return response

        monkeypatch.setattr("services.method_service.requests.Session", FakeSession)

        manifest = download_reports(
            {
                "cookie_dump_path": str(cookie_dump_path),
                "manifest_path": str(manifest_path),
                "output_dir": str(output_dir),
                "reports": [
                    {
                        "name": "地市作战",
                        "stage": "city_ops",
                        "method": "POST",
                        "url": "https://example/json",
                        "body_type": "json",
                        "data": {"areaId": "AQ726"},
                        "response_mode": "json_to_excel",
                        "excel": {
                            "data_path": "result.tableData",
                            "sheet_name": "地市作战明细",
                            "columns": [{"field": "areaName", "header": "名称"}],
                        },
                    }
                ],
            }
        )

        result = manifest["results"][0]
        assert result["response_mode"] == "json_to_excel"
        assert result["rows"] == 1
        assert Path(result["output_path"]).exists()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


