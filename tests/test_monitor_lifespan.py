from __future__ import annotations

import asyncio

import backend.app as app_module

from backend.services.monitor_stream import MonitorRealtimeStatus


class FakeMonitorSyncLoop:
    instance: FakeMonitorSyncLoop | None = None

    def __init__(self, **callbacks) -> None:
        self.callbacks = callbacks
        type(self).instance = self

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


def test_lifespan_broadcasts_reconciliation_failure_to_stream_clients(monkeypatch) -> None:
    updates: list[dict[str, object]] = []

    monkeypatch.setattr(app_module, "MonitorSyncLoop", FakeMonitorSyncLoop)
    monkeypatch.setattr(app_module, "realtime_status", MonitorRealtimeStatus())
    monkeypatch.setattr(app_module, "dispose_dashboard_engine", lambda: None)
    monkeypatch.setattr(app_module.stream_hub, "bind_loop", lambda _loop: None)
    monkeypatch.setattr(
        app_module.stream_hub,
        "publish_payload_from_thread",
        updates.append,
    )

    async def scenario() -> None:
        async with app_module.lifespan(app_module.app):
            assert FakeMonitorSyncLoop.instance is not None
            FakeMonitorSyncLoop.instance.callbacks["on_error"](
                OSError("authorization=secret Prefect sync timed out")
            )

    asyncio.run(scenario())

    assert updates[0]["type"] == "upstream.updated"
    assert updates[0]["upstream"]["lastErrorCategory"] == "RECONCILIATION_FAILED"
    assert updates[0]["upstream"]["lastErrorDetail"] == "authorization=[已隐藏] Prefect sync timed out"


def test_lifespan_broadcasts_reconciliation_success_to_stream_clients(monkeypatch) -> None:
    updates: list[dict[str, object]] = []

    monkeypatch.setattr(app_module, "MonitorSyncLoop", FakeMonitorSyncLoop)
    monkeypatch.setattr(app_module, "realtime_status", MonitorRealtimeStatus())
    monkeypatch.setattr(app_module, "dispose_dashboard_engine", lambda: None)
    monkeypatch.setattr(app_module.stream_hub, "bind_loop", lambda _loop: None)
    monkeypatch.setattr(
        app_module.stream_hub,
        "publish_payload_from_thread",
        updates.append,
    )

    async def scenario() -> None:
        async with app_module.lifespan(app_module.app):
            assert FakeMonitorSyncLoop.instance is not None
            FakeMonitorSyncLoop.instance.callbacks["on_reconciled"]()

    asyncio.run(scenario())

    assert updates[0]["type"] == "upstream.updated"
    assert updates[0]["upstream"]["lastReconciledAt"] is not None
    assert updates[0]["upstream"]["lastErrorCategory"] is None
