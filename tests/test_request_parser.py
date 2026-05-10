import json

from utils.request_parser import headers_from_header_rows, parse_request_by_mode, parse_request_parts


def test_parse_full_json_request_and_filters_browser_headers():
    parsed = parse_request_parts(
        raw_request=(
            "POST /zhyypt/smop/export/exportData HTTP/1.1\n"
            "Host: usm.ha.cmcc:19011\n"
            "Content-Type: application/json\n"
            "Cookie: JSESSIONID=abc\n"
            "user-info: token-value\n"
            "sec-fetch-mode: cors\n"
            "\n"
            '{"date":"${today}"}'
        )
    )

    assert parsed["method"] == "POST"
    assert parsed["url"] == "https://usm.ha.cmcc:19011/zhyypt/smop/export/exportData"
    assert parsed["body_type"] == "json"
    assert parsed["data"] == {"date": "${today}"}

    selected = headers_from_header_rows(parsed["header_rows"])
    assert selected["Content-Type"] == "application/json"
    assert selected["user-info"] == "token-value"
    assert "Cookie" not in selected
    assert "Host" not in selected
    assert "sec-fetch-mode" not in selected


def test_parse_form_body_and_merge_query():
    parsed = parse_request_parts(
        method="POST",
        url="https://example/export?area=371",
        headers_text="Content-Type: application/x-www-form-urlencoded",
        body="templateId=85479&queryDate=${yesterday}",
    )

    assert parsed["body_type"] == "form"
    assert parsed["data"] == {
        "templateId": "85479",
        "queryDate": "${yesterday}",
        "area": "371",
    }


def test_parse_raw_body_for_multipart():
    body = "------WebKitFormBoundary\r\nContent-Disposition: form-data"
    parsed = parse_request_parts(
        method="POST",
        url="https://example/upload",
        headers_text="Content-Type: multipart/form-data; boundary=----WebKitFormBoundary",
        body=body,
    )

    assert parsed["body_type"] == "raw"
    assert parsed["raw_body"] == body
    assert json.dumps(parsed["data"], ensure_ascii=False) == "{}"


def test_parse_headers_in_raw_request_and_body_from_separate_field():
    parsed = parse_request_parts(
        raw_request="Accept: application/json\nContent-Type: application/json\nuser-info: abc",
        method="POST",
        url="https://example/export",
        body='{\\"areaId\\":\\"AQ\\",\\"rankDown\\":null}',
    )

    assert parsed["url"] == "https://example/export"
    assert parsed["body_type"] == "json"
    assert parsed["data"] == {"areaId": "AQ", "rankDown": None}
    assert parsed["headers"]["user-info"] == "abc"


def test_parse_curl_bash_request():
    parsed = parse_request_by_mode(
        "curl",
        raw_request=(
            "curl 'https://example/export' \\\n"
            "  -H 'Content-Type: application/json' \\\n"
            "  -H 'user-info: abc' \\\n"
            "  --data-raw '{\"queryDate\":\"${today}\"}'"
        )
    )

    assert parsed["method"] == "POST"
    assert parsed["url"] == "https://example/export"
    assert parsed["headers"]["user-info"] == "abc"
    assert parsed["body_type"] == "json"
    assert parsed["data"] == {"queryDate": "${today}"}


def test_parse_fetch_request():
    parsed = parse_request_by_mode(
        "fetch",
        raw_request=(
            'fetch("https://example/export", {'
            'method: "POST", '
            'headers: {"Content-Type": "application/json", "user-info": "abc"}, '
            'body: "{\\"areaId\\":\\"AQ\\"}"'
            "});"
        )
    )

    assert parsed["method"] == "POST"
    assert parsed["url"] == "https://example/export"
    assert parsed["headers"]["Content-Type"] == "application/json"
    assert parsed["data"] == {"areaId": "AQ"}


def test_parse_powershell_request():
    parsed = parse_request_by_mode(
        "powershell",
        raw_request=(
            "Invoke-WebRequest -Uri 'https://example/export' -Method POST "
            "-Headers @{'Content-Type'='application/json'; 'user-info'='abc'} "
            "-Body '{\"areaId\":\"AQ\"}'"
        )
    )

    assert parsed["method"] == "POST"
    assert parsed["url"] == "https://example/export"
    assert parsed["headers"]["user-info"] == "abc"
    assert parsed["data"] == {"areaId": "AQ"}


def test_parse_har_request():
    parsed = parse_request_by_mode(
        "har",
        raw_request=json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "method": "POST",
                                "url": "https://example/export",
                                "headers": [
                                    {"name": "Content-Type", "value": "application/json"},
                                    {"name": "user-info", "value": "abc"},
                                ],
                                "postData": {"text": "{\"areaId\":\"AQ\"}"},
                            }
                        }
                    ]
                }
            }
        )
    )

    assert parsed["method"] == "POST"
    assert parsed["url"] == "https://example/export"
    assert parsed["headers"]["user-info"] == "abc"
    assert parsed["data"] == {"areaId": "AQ"}


def test_parse_headers_body_mode_uses_separate_fields():
    parsed = parse_request_by_mode(
        "headers_body",
        method="POST",
        url="https://example/export",
        headers_text="Content-Type: application/json\nuser-info: abc",
        body='{"areaId":"AQ"}',
    )

    assert parsed["method"] == "POST"
    assert parsed["url"] == "https://example/export"
    assert parsed["headers"]["user-info"] == "abc"
    assert parsed["data"] == {"areaId": "AQ"}
