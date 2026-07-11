from datetime import datetime

import pytest

from utils.date_placeholders import resolve_dynamic_placeholders


def test_resolve_date_expression_supports_month_and_year_offsets():
    now = datetime(2026, 7, 8, 9, 5, 0)

    assert resolve_dynamic_placeholders("${date:yesterday|yyyyMMdd}", now=now) == "20260707"
    assert resolve_dynamic_placeholders("${date:yesterday-1M|yyyyMMdd}", now=now) == "20260607"
    assert resolve_dynamic_placeholders("${date:yesterday-1y|yyyyMMdd}", now=now) == "20250707"
    assert resolve_dynamic_placeholders("${date:today-7d|yyyy-MM-dd}", now=now) == "2026-07-01"


def test_resolve_date_expression_clamps_missing_day_to_month_end():
    assert (
        resolve_dynamic_placeholders(
            "${date:yesterday-1M|yyyyMMdd}",
            now=datetime(2026, 4, 1, 9, 0, 0),
        )
        == "20260228"
    )
    assert (
        resolve_dynamic_placeholders(
            "${date:yesterday-1M|yyyyMMdd}",
            now=datetime(2024, 4, 1, 9, 0, 0),
        )
        == "20240229"
    )


def test_resolve_date_expression_rejects_invalid_format():
    with pytest.raises(ValueError, match="不支持的日期格式"):
        resolve_dynamic_placeholders("${date:yesterday|yyyymmdd}", now=datetime(2026, 7, 8))
