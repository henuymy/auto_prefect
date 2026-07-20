from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import monitor
from backend.services.prefect_monitor_event_service import ProcessedPrefectEvent


EVENT = {
    "id": "event-1",
    "occurred": "2026-07-19T09:02:00Z",
    "event": "prefect.flow-run.Completed",
    "resource": {"prefect.resource.id": "prefect.flow-run.run-1"},
    "related": [],
    "payload": {},
}


def client() -> TestClient:
    app = FastAPI()
    app.include_router(monitor.router)
    return TestClient(app)


def test_prefect_webhook_rejects_missing_or_invalid_secret(monkeypatch) -> None:
    monkeypatch.setenv("PREFECT_MONITOR_WEBHOOK_SECRET", "test-secret")

    with client() as test_client:
        missing = test_client.post("/api/monitor/events/prefect", json=EVENT)
        invalid = test_client.post(
            "/api/monitor/events/prefect",
            json=EVENT,
            headers={"X-Prefect-Monitor-Secret": "wrong"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_prefect_webhook_publishes_only_after_accepted_event(monkeypatch) -> None:
    published: list[str] = []

    def publish(run_id: str) -> None:
        published.append(run_id)

    monkeypatch.setenv("PREFECT_MONITOR_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(
        monitor,
        "_process_prefect_event",
        lambda payload: ProcessedPrefectEvent(
            accepted=True,
            duplicate=False,
            run={"id": "mon_report_run_1"},
        ),
        raising=False,
    )
    monkeypatch.setattr(monitor.stream_hub, "publish_background", publish, raising=False)

    with client() as test_client:
        response = test_client.post(
            "/api/monitor/events/prefect",
            json=EVENT,
            headers={"X-Prefect-Monitor-Secret": "test-secret"},
        )

    assert response.status_code == 202
    assert response.json() == {"accepted": True, "duplicate": False}
    assert published == ["mon_report_run_1"]


def test_prefect_webhook_does_not_publish_duplicate_event(monkeypatch) -> None:
    published: list[str] = []

    def publish(run_id: str) -> None:
        published.append(run_id)

    monkeypatch.setenv("PREFECT_MONITOR_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(
        monitor,
        "_process_prefect_event",
        lambda payload: ProcessedPrefectEvent(
            accepted=False,
            duplicate=True,
            run={"id": "mon_report_run_1"},
        ),
        raising=False,
    )
    monkeypatch.setattr(monitor.stream_hub, "publish_background", publish, raising=False)

    with client() as test_client:
        response = test_client.post(
            "/api/monitor/events/prefect",
            json=EVENT,
            headers={"X-Prefect-Monitor-Secret": "test-secret"},
        )

    assert response.status_code == 202
    assert response.json() == {"accepted": False, "duplicate": True}
    assert published == []
