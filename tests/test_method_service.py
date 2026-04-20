import json
import shutil
from pathlib import Path
from uuid import uuid4

from services.method_service import (
    build_headers,
    download_reports,
    filename_from_content_disposition,
    find_stage,
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
        "csrf_headers_from_cookies": {"ssr-header": "ssr-token"},
    }

    stage = find_stage(cookie_dump, "report_analysis")
    headers = build_headers(report, stage)

    assert headers["ssr-token"] == "token-value"
    assert headers["X-CSRF-TOKEN"] == "token-value"


def test_filename_from_content_disposition_utf8():
    filename = filename_from_content_disposition(
        "attachment; filename*=UTF-8''%E6%97%A5%E9%80%9A%E6%8A%A5.xls",
        "https://example/export",
    )

    assert filename == "日通报.xls"


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
