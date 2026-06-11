from __future__ import annotations

from scripts.dashboard.export_metric_sample import classify_value, enrich_rows


def test_classify_value_preserves_raw_value_categories():
    assert classify_value("12")[0] == "INTEGER"
    assert classify_value("12.50")[0] == "DECIMAL"
    assert classify_value("--")[0] == "EMPTY"
    assert classify_value(None)[0] == "EMPTY"
    assert classify_value("异常")[0] == "NON_NUMERIC"


def test_enrich_rows_identifies_self_summary_and_formal_area():
    rows = enrich_rows(
        [
            {
                "响应层级索引": 0,
                "上级请求编码": "",
                "本次请求areaId": "A",
                "返回区域编码": "A",
                "返回区域名称": "郑州市",
                "原始指标值": "10",
            },
            {
                "响应层级索引": 2,
                "上级请求编码": "AQ",
                "本次请求areaId": "AQ701",
                "返回区域编码": "13800000000&AQ701",
                "返回区域名称": "渠道经理",
                "原始指标值": "1",
            },
        ]
    )

    assert rows[0]["node_type"] == "CITY"
    assert rows[0]["is_self_summary"] == "是"
    assert rows[0]["is_dashboard_area"] == "是"
    assert rows[1]["node_type"] == "CHANNEL_MANAGER"
    assert rows[1]["is_dashboard_area"] == "否"
