from __future__ import annotations

import json

import pytest

from datetime import date

from services.dashboard_acc_pipeline import (
    load_acc_collection_report,
    month_end,
)


def test_load_daily_collection_report_selects_named_city_ops(tmp_path):
    path = tmp_path / "reports.json"
    path.write_text(
        json.dumps(
            {
                "downloads": [
                    {
                        "name": "实时",
                        "stage": "city_ops",
                        "enabled": True,
                    },
                    {
                        "name": "日累计",
                        "stage": "city_ops",
                        "enabled": True,
                        "url": "https://example/daily",
                    },
                    {
                        "name": "日累计",
                        "stage": "smart_ops",
                        "enabled": True,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = load_acc_collection_report(path)

    assert result["url"] == "https://example/daily"
    assert result["response_mode"] == "json_to_excel"


def test_load_daily_collection_report_requires_exact_match(tmp_path):
    path = tmp_path / "reports.json"
    path.write_text('{"downloads": []}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="count=0"):
        load_acc_collection_report(path)


def test_month_end_normalizes_monthly_stat_date():
    assert month_end(date(2026, 5, 1)) == date(2026, 5, 31)
    assert month_end(date(2024, 2, 12)) == date(2024, 2, 29)
