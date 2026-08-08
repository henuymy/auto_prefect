from __future__ import annotations

from backend.app import app
from backend.routers import dashboard


def test_presence_heartbeat_forwards_the_connection_id(monkeypatch):
    captured: list[str] = []
    monkeypatch.setattr(
        dashboard,
        "record_dashboard_presence",
        lambda connection_id: captured.append(connection_id) or {"active_connections": 3},
    )

    result = dashboard.dashboard_presence_heartbeat(
        dashboard.DashboardPresencePayload(connection_id="connection-alpha-0001")
    )

    assert result == {"active_connections": 3}
    assert captured == ["connection-alpha-0001"]


def test_presence_heartbeat_is_exposed_by_the_dashboard_api():
    assert "post" in app.openapi()["paths"]["/api/dashboard/presence/heartbeat"]
