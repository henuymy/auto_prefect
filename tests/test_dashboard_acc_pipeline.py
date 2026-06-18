from __future__ import annotations

from datetime import date

from services.dashboard_pipeline import month_end


def test_month_end_normalizes_monthly_stat_date():
    assert month_end(date(2026, 5, 1)) == date(2026, 5, 31)
    assert month_end(date(2024, 2, 12)) == date(2024, 2, 29)
