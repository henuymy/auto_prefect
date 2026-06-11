from __future__ import annotations

from backend.services import dashboard_runner


class Completed:
    returncode = 0
    stdout = "Created flow run 'test' with id 123e4567-e89b-12d3-a456-426614174000"
    stderr = ""


def test_submit_dashboard_collection_uses_prefect_deployment(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(dashboard_runner.subprocess, "run", fake_run)

    result = dashboard_runner.submit_dashboard_collection(force_refresh=True)

    command = calls[0][0]
    assert command[3:6] == [
        "deployment",
        "run",
        "dashboard-metric-flow/dashboard-collection",
    ]
    assert "trigger_type=MANUAL" in command
    assert "force_refresh=true" in command
    assert result["status"] == "submitted"
    assert result["flow_run_id"] == "123e4567-e89b-12d3-a456-426614174000"
