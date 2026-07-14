import pytest

from flows import dashboard_metric_flow as dashboard_flow
from services.session_manager import SessionInfrastructureError, SessionLoginError


def test_dashboard_flow_retries_collection_once_with_force_refresh(monkeypatch):
    calls = []

    def fake_task(**kwargs):
        calls.append(kwargs["force_refresh"])
        if len(calls) == 1:
            raise RuntimeError("session expired: HTTP 302")
        return {"batch_no": "batch-1", "timing": {}}

    monkeypatch.setattr(dashboard_flow, "run_dashboard_metric_task", fake_task)

    result = dashboard_flow.run_dashboard_metric_with_session_refresh(
        mode="REALTIME",
        config_path="config/dashboard/session.json",
        trigger_type="SCHEDULED",
        force_refresh=False,
    )

    assert result["batch_no"] == "batch-1"
    assert calls == [False, True]


def test_dashboard_flow_does_not_refresh_for_network_failure(monkeypatch):
    calls = []

    def fake_task(**kwargs):
        calls.append(kwargs["force_refresh"])
        raise RuntimeError("ConnectTimeout")

    monkeypatch.setattr(dashboard_flow, "run_dashboard_metric_task", fake_task)

    with pytest.raises(RuntimeError, match="ConnectTimeout"):
        dashboard_flow.run_dashboard_metric_with_session_refresh(
            mode="REALTIME",
            config_path="config/dashboard/session.json",
            trigger_type="SCHEDULED",
            force_refresh=False,
        )

    assert calls == [False]


def test_dashboard_retries_then_reraises_second_auth_failure(monkeypatch):
    second_failure = RuntimeError("session expired: HTTP 403")
    outcomes = iter([RuntimeError("session expired: HTTP 302"), second_failure])
    calls = []

    def fake_task(**kwargs):
        calls.append(kwargs["force_refresh"])
        raise next(outcomes)

    monkeypatch.setattr(dashboard_flow, "run_dashboard_metric_task", fake_task)

    with pytest.raises(RuntimeError) as exc_info:
        dashboard_flow.run_dashboard_metric_with_session_refresh(
            mode="REALTIME", config_path="config/dashboard/session.json", trigger_type="SCHEDULED", force_refresh=False
        )

    assert exc_info.value is second_failure
    assert calls == [False, True]


def test_dashboard_initial_forced_mode_reraises_without_retry(monkeypatch):
    failure = RuntimeError("session expired: HTTP 401")
    calls = []

    def fake_task(**kwargs):
        calls.append(kwargs["force_refresh"])
        raise failure

    monkeypatch.setattr(dashboard_flow, "run_dashboard_metric_task", fake_task)

    with pytest.raises(RuntimeError) as exc_info:
        dashboard_flow.run_dashboard_metric_with_session_refresh(
            mode="REALTIME", config_path="config/dashboard/session.json", trigger_type="MANUAL", force_refresh=True
        )

    assert exc_info.value is failure
    assert calls == [True]
