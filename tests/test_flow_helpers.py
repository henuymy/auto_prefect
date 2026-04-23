import pytest

from flows.notify_single_flow import (
    build_download_config,
    parse_wait_for_change_config,
    resolve_dynamic_placeholders,
    select_downloaded_report_path,
)


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


def test_parse_wait_for_change_config_defaults_disabled():
    cfg = parse_wait_for_change_config({})
    assert cfg["enabled"] is False
    assert cfg["poll_interval_seconds"] == 300
    assert cfg["max_wait_seconds"] is None


def test_parse_wait_for_change_config_with_timeout():
    cfg = parse_wait_for_change_config(
        {
            "wait_for_change": {
                "enabled": True,
                "poll_interval_seconds": 120,
                "max_wait_minutes": 10,
            }
        }
    )
    assert cfg["enabled"] is True
    assert cfg["poll_interval_seconds"] == 120
    assert cfg["max_wait_seconds"] == 600


def test_parse_wait_for_change_config_rejects_invalid_poll_interval():
    with pytest.raises(ValueError, match="poll_interval_seconds"):
        parse_wait_for_change_config({"wait_for_change": {"enabled": True, "poll_interval_seconds": 0}})


def test_parse_wait_for_change_config_rejects_invalid_max_wait():
    with pytest.raises(ValueError, match="max_wait_minutes"):
        parse_wait_for_change_config({"wait_for_change": {"enabled": True, "max_wait_minutes": 0}})


def test_resolve_dynamic_placeholders_supports_today_and_hour():
    from datetime import datetime

    now = datetime(2026, 3, 14, 16, 30, 0)
    assert resolve_dynamic_placeholders("${today}", now=now) == "2026-03-14"
    assert resolve_dynamic_placeholders("${yesterday}", now=now) == "2026-03-13"
    assert resolve_dynamic_placeholders("${yesterday_yyyymmdd}", now=now) == "20260313"
    assert resolve_dynamic_placeholders("${hour}", now=now) == "16"
    assert resolve_dynamic_placeholders("${hour2}", now=now) == "16"


def test_build_download_config_resolves_dynamic_tokens():
    base = {
        "cookie_dump_path": "runtime/cookies/cookie_dump.json",
        "report_defaults": {
            "enabled": True,
            "method": "POST",
            "url": "https://example/export",
        },
    }
    report_cfg = {
        "name": "PK小时通报",
        "download": {
            "data": {
                "queryDate": "${yesterday}",
                "versionName": "${yesterday_yyyymmdd}",
                "hour": "${hour}",
                "queryHour2": "${hour2}",
            }
        },
    }

    config = build_download_config(base, report_cfg)
    report = config["reports"][0]

    assert report["data"]["queryDate"] != "${yesterday}"
    assert report["data"]["versionName"] != "${yesterday_yyyymmdd}"
    assert report["data"]["hour"] != "${hour}"
    assert report["data"]["queryHour2"] != "${hour2}"
