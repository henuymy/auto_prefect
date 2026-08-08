from __future__ import annotations

import pytest

from services.dashboard_presence_service import (
    clear_dashboard_presence,
    record_dashboard_presence,
)


@pytest.fixture(autouse=True)
def reset_presence_state():
    clear_dashboard_presence()
    yield
    clear_dashboard_presence()


def test_heartbeat_counts_distinct_active_connections_and_refreshes_existing_one():
    first = "connection-alpha-0001"
    second = "connection-bravo-0002"

    assert record_dashboard_presence(first, now=100.0) == {"active_connections": 1}
    assert record_dashboard_presence(first, now=120.0) == {"active_connections": 1}
    assert record_dashboard_presence(second, now=121.0) == {"active_connections": 2}


def test_heartbeat_removes_connections_that_stopped_reporting():
    assert record_dashboard_presence("connection-alpha-0001", now=100.0) == {
        "active_connections": 1
    }
    assert record_dashboard_presence("connection-bravo-0002", now=146.0) == {
        "active_connections": 1
    }


@pytest.mark.parametrize("connection_id", ["too-short", "has space-0001", "invalid!000000000"])
def test_heartbeat_rejects_invalid_connection_ids(connection_id: str):
    with pytest.raises(ValueError, match="格式无效"):
        record_dashboard_presence(connection_id, now=100.0)
