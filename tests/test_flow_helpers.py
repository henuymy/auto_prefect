import pytest
from pathlib import Path

from flows.notify_single_flow import (
    aggregate_compare_results,
    build_download_config,
    build_compare_source_configs,
    parse_wait_for_change_config,
    resolve_dynamic_placeholders,
    select_downloaded_report_path,
)

PROJECT_TEST_RUNTIME_DIR = Path("runtime/flow/test")


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


def test_build_download_config_supports_multiple_downloads():
    base = {
        "report_defaults": {
            "enabled": True,
            "method": "POST",
            "url": "https://example/export",
        },
    }
    report_cfg = {
        "name": "多源通报",
        "downloads": [
            {"name": "报表A", "data": {"sheetName": "A"}},
            {"name": "报表B", "data": {"sheetName": "B"}},
        ],
    }

    config = build_download_config(base, report_cfg)

    assert [item["name"] for item in config["reports"]] == ["报表A", "报表B"]
    assert config["reports"][0]["method"] == "POST"
    assert config["reports"][1]["data"]["sheetName"] == "B"


def test_build_compare_source_configs_maps_download_outputs():
    manifest = {
        "results": [
            {"name": "报表A", "output_path": "runtime/downloads/a.xls"},
            {"name": "报表B", "output_path": "runtime/downloads/b.xls"},
        ]
    }
    report_cfg = {
        "template_path": "templates/template.xlsx",
        "compare_sources": [
            {
                "download_name": "报表A",
                "sheet_mappings": [{"new_sheet_name": "A1", "template_sheet_name": "T1"}],
            },
            {
                "download_name": "报表B",
                "sheet_mappings": [{"new_sheet_name": "B1", "template_sheet_name": "T2"}],
            },
        ],
    }

    configs = build_compare_source_configs(
        {"sample_limit": 10},
        report_cfg,
        manifest,
        {},
        PROJECT_TEST_RUNTIME_DIR,
    )

    assert [item["download_name"] for item in configs] == ["报表A", "报表B"]
    assert configs[0]["config"]["new_report_path"].endswith("a.xls")
    assert configs[1]["config"]["sheet_mappings"][0]["template_sheet_name"] == "T2"


def test_aggregate_compare_results_all_changed_requires_every_sheet_changed():
    compare_runs = [
        {
            "download_name": "报表A",
            "source_report_path": "a.xls",
            "config": {"new_report_path": "a.xls"},
            "result": {
                "sheets": [
                    {"name": "A1", "new_sheet_name": "A1", "template_sheet_name": "T1", "result": "changed"},
                    {"name": "A2", "new_sheet_name": "A2", "template_sheet_name": "T2", "result": "same"},
                ]
            },
        }
    ]

    result = aggregate_compare_results(compare_runs, update_condition="all_changed")

    assert result["result"] == "same"
    assert result["summary"]["changed"] == 1
    assert result["sheets"][0]["source_report_path"] == "a.xls"


def test_aggregate_compare_results_all_changed_passes_when_all_changed():
    compare_runs = [
        {
            "download_name": "报表A",
            "source_report_path": "a.xls",
            "config": {"new_report_path": "a.xls"},
            "result": {
                "sheets": [
                    {"name": "A1", "new_sheet_name": "A1", "template_sheet_name": "T1", "result": "changed"},
                ]
            },
        }
    ]

    result = aggregate_compare_results(compare_runs, update_condition="all_changed")

    assert result["result"] == "changed"
    assert result["summary"]["total"] == 1
