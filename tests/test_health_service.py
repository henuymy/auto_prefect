from __future__ import annotations

import json

from backend import app as backend_app
from backend.services import health_service


def test_readiness_status_requires_all_dependencies(monkeypatch):
    monkeypatch.setattr(
        health_service, "check_dashboard_mysql", lambda: {"ok": True}
    )
    monkeypatch.setattr(
        health_service.prefect_runner,
        "check_prefect_status",
        lambda: {"ok": False, "message": "offline"},
    )
    monkeypatch.setattr(
        health_service, "check_runtime_storage", lambda: {"ok": True}
    )

    result = health_service.readiness_status()

    assert result["ok"] is False
    assert result["checks"]["prefect"]["ok"] is False


def test_health_returns_503_when_not_ready(monkeypatch):
    monkeypatch.setattr(
        backend_app,
        "readiness_status",
        lambda: {"ok": False, "checks": {"dashboard_mysql": {"ok": False}}},
    )

    response = backend_app.health()

    assert response.status_code == 503
    assert json.loads(response.body)["ok"] is False


def test_live_is_independent_from_dependencies():
    assert backend_app.live() == {"ok": True}


def test_runtime_storage_probe_is_writable(monkeypatch, tmp_path):
    monkeypatch.setattr(health_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("HEALTH_MIN_FREE_BYTES", "1")

    result = health_service.check_runtime_storage()

    assert result["ok"] is True
    assert result["writable"] is True
    assert list((tmp_path / "runtime" / "health").iterdir()) == []
