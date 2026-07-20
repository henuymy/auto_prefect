from datetime import datetime, timezone

from backend.services.mysql_monitor_store import _iso, _mysql_datetime


def test_mysql_monitor_store_normalizes_aware_prefect_times_to_utc_naive() -> None:
    value = _mysql_datetime(datetime.fromisoformat("2026-07-19T21:41:00+08:00"))

    assert value == datetime(2026, 7, 19, 13, 41, 0)
    assert value.tzinfo is None
    assert _mysql_datetime(datetime(2026, 7, 19, 13, 41, 0, tzinfo=timezone.utc)) == value


def test_mysql_monitor_store_serializes_utc_naive_times_with_an_explicit_offset() -> None:
    assert _iso(datetime(2026, 7, 19, 15, 1, 53)) == "2026-07-19T15:01:53+00:00"
