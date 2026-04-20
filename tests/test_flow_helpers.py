import pytest

from flows.notify_single_flow import select_downloaded_report_path


def test_select_downloaded_report_path_by_name():
    manifest = {
        "results": [
            {"name": "其他报表", "output_path": "runtime/downloads/other.xls"},
            {"name": "日通报导出", "output_path": "runtime/downloads/daily.xls"},
        ]
    }

    assert select_downloaded_report_path(manifest, report_name="日通报导出") == "runtime/downloads/daily.xls"


def test_select_downloaded_report_path_uses_first_output_when_name_not_set():
    manifest = {
        "results": [
            {"name": "日通报导出", "output_path": "runtime/downloads/daily.xls"},
        ]
    }

    assert select_downloaded_report_path(manifest) == "runtime/downloads/daily.xls"


def test_select_downloaded_report_path_requires_output_path():
    manifest = {
        "results": [
            {"name": "日通报导出", "dry_run": True},
        ]
    }

    with pytest.raises(RuntimeError, match="output_path"):
        select_downloaded_report_path(manifest, report_name="日通报导出")
