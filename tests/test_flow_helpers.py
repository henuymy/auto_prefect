import pytest
from pathlib import Path

from flows.notify_single_flow import (
    aggregate_compare_results,
    build_download_config,
    build_compare_source_configs,
    build_login_config,
    is_session_expired_error,
    parse_wait_for_change_config,
    required_stages_for_report,
    resolve_dynamic_placeholders,
    select_downloaded_report_path,
    should_send_when_same,
)

PROJECT_TEST_RUNTIME_DIR = Path("runtime/flow/test")


def test_is_session_expired_error_matches_known_messages():
    assert is_session_expired_error(RuntimeError("JSON 接口返回 session 已过期: reCode=1101"))
    assert is_session_expired_error(RuntimeError("reason=session_expired"))
    assert is_session_expired_error(RuntimeError("reMsg=单点登录超时，请登录后重新跳转"))
    assert is_session_expired_error(RuntimeError("下载响应为 HTML（可能是登录页）"))
    assert not is_session_expired_error(RuntimeError("Connection timed out"))


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


def test_should_send_when_same_reads_template_update_flag():
    assert should_send_when_same({"template_update": {"send_when_same": True}}) is True
    assert should_send_when_same({"template_update": {"send_when_same": False}}) is False
    assert should_send_when_same({}) is False


def test_resolve_dynamic_placeholders_supports_today_and_hour():
    from datetime import datetime

    now = datetime(2026, 3, 14, 16, 30, 0)
    assert resolve_dynamic_placeholders("${today}", now=now) == "2026-03-14"
    assert resolve_dynamic_placeholders("${today_yyyymmdd}", now=now) == "20260314"
    assert resolve_dynamic_placeholders("${yesterday}", now=now) == "2026-03-13"
    assert resolve_dynamic_placeholders("${yesterday_yyyymmdd}", now=now) == "20260313"
    assert resolve_dynamic_placeholders("${hour}", now=now) == "16"
    assert resolve_dynamic_placeholders("${hour2}", now=now) == "16"


def test_build_download_config_resolves_dynamic_tokens():
    base = {
        "cookie_dump_path": "runtime/cookies/cookie_dump.json",
        "report_defaults": {},
    }
    report_cfg = {
        "name": "PK小时通报",
        "downloads": [
            {
                "name": "PK小时通报",
                "stage": "report_analysis",
                "method": "POST",
                "url": "https://example/export",
                "body_type": "json",
                "response_mode": "file",
                "data": {
                    "queryDate": "${yesterday}",
                    "versionName": "${yesterday_yyyymmdd}",
                    "hour": "${hour}",
                    "queryHour2": "${hour2}",
                },
            }
        ],
    }

    config = build_download_config(base, report_cfg)
    report = config["reports"][0]

    assert report["data"]["queryDate"] != "${yesterday}"
    assert report["data"]["versionName"] != "${yesterday_yyyymmdd}"
    assert report["data"]["hour"] != "${hour}"
    assert report["data"]["queryHour2"] != "${hour2}"


def test_build_download_config_supports_multiple_downloads():
    base = {
        "report_defaults": {},
    }
    report_cfg = {
        "name": "多源通报",
        "downloads": [
            {
                "name": "报表A",
                "stage": "report_analysis",
                "method": "POST",
                "url": "https://example/export-a",
                "body_type": "json",
                "response_mode": "file",
                "data": {"sheetName": "A"},
            },
            {
                "name": "报表B",
                "stage": "report_analysis",
                "method": "POST",
                "url": "https://example/export-b",
                "body_type": "json",
                "response_mode": "file",
                "data": {"sheetName": "B"},
            },
        ],
    }

    config = build_download_config(base, report_cfg)

    assert [item["name"] for item in config["reports"]] == ["报表A", "报表B"]
    assert config["reports"][0]["method"] == "POST"
    assert config["reports"][1]["data"]["sheetName"] == "B"


def test_required_stages_for_report_uses_enabled_downloads_only():
    report_cfg = {
        "downloads": [
            {"name": "A", "stage": "city_ops", "enabled": True},
            {"name": "B", "stage": "smart_ops", "enabled": False},
            {"name": "C", "stage": "city_ops"},
            {"name": "D", "stage": "report_analysis"},
            {
                "source": "tencent_sheet",
                "name": "腾讯文档",
                "doc_url": "https://docs.qq.com/sheet/DY1h4R1Rmd0FwWFhF?tab=000002",
                "sheets": [{"sheet_id": "000002", "range": "A1:B2"}],
            },
        ]
    }

    assert required_stages_for_report(report_cfg) == ["city_ops", "report_analysis"]


def test_build_login_config_overrides_required_stages_from_report():
    base = {
        "required_stages": ["report_analysis", "smart_ops", "city_ops", "data_market"],
        "stage_probes": {"city_ops": {"enabled": True}},
    }
    report_cfg = {
        "downloads": [
            {"name": "A", "stage": "city_ops"},
        ]
    }

    config = build_login_config(base, report_cfg)

    assert config["required_stages"] == ["city_ops"]
    assert config["stage_probes"] == {"city_ops": {"enabled": True}}


def test_build_download_config_complete_request_does_not_inherit_ssr_headers():
    base = {
        "report_defaults": {},
    }
    report_cfg = {
        "downloads": [
            {
                "name": "智慧运营",
                "stage": "smart_ops",
                "method": "POST",
                "url": "https://example/exportData",
                "headers": {
                    "Content-Type": "application/json",
                    "User-Info": "abc",
                },
                "headers_from_session_storage": {"User-Info": "zhyyptInfo.accessToken"},
                "body_type": "json",
                "response_mode": "file",
                "data": {"date": "${today}"},
                "allow_redirects": True,
            }
        ]
    }

    config = build_download_config(base, report_cfg)
    report = config["reports"][0]

    assert report["headers"] == {
        "Content-Type": "application/json",
        "User-Info": "abc",
    }
    assert report["headers_from_session_storage"] == {"User-Info": "zhyyptInfo.accessToken"}
    assert "headers_from_cookies" not in report
    assert "csrf_headers_from_cookies" not in report
    assert report["allow_redirects"] is True


def test_build_download_config_rejects_legacy_download_key():
    with pytest.raises(ValueError, match="旧字段 download"):
        build_download_config(
            {"report_defaults": {}},
            {
                "name": "旧配置",
                "download": {"name": "旧下载"},
            },
        )


def test_build_download_config_requires_schema_fields():
    with pytest.raises(ValueError, match="缺少必填字段 response_mode"):
        build_download_config(
            {"report_defaults": {}},
            {
                "name": "缺字段",
                "downloads": [
                    {
                        "name": "下载",
                        "stage": "report_analysis",
                        "method": "POST",
                        "url": "https://example/export",
                        "body_type": "json",
                    }
                ],
            },
        )


def test_build_download_config_accepts_tencent_sheet_source_without_stage():
    config = build_download_config(
        {"report_defaults": {}, "output_dir": "runtime/downloads"},
        {
            "name": "腾讯文档通报",
            "downloads": [
                {
                    "source": "tencent_sheet",
                    "name": "腾讯文档日报",
                    "doc_url": "https://docs.qq.com/sheet/DY1h4R1Rmd0FwWFhF?tab=000002",
                    "sheets": [{"sheet_id": "000002", "range": "A1:B2", "output_sheet_name": "日报"}],
                }
            ],
        },
    )

    report = config["reports"][0]
    assert report["source"] == "tencent_sheet"
    assert report["sheets"][0]["sheet_id"] == "000002"
    assert "stage" not in report



def test_build_compare_source_configs_maps_download_outputs():
    manifest = {
        "results": [
            {"name": "报表A", "output_path": "runtime/downloads/a.xls"},
            {"name": "报表B", "output_path": "runtime/downloads/b.xls"},
        ]
    }
    report_cfg = {
        "template_path": "templates/template.xlsx",
        "downloads": [
            {
                "name": "报表A",
                "stage": "report_analysis",
                "method": "POST",
                "url": "https://example/export-a",
                "body_type": "json",
                "response_mode": "file",
            },
            {
                "name": "报表B",
                "stage": "report_analysis",
                "method": "POST",
                "url": "https://example/export-b",
                "body_type": "json",
                "response_mode": "file",
            },
        ],
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


