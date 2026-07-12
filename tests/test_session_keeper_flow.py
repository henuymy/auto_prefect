import pytest

from flows import session_keeper_flow as keeper_module
from services.session_manager import SessionInfrastructureError, SessionLoginError


def test_keeper_reuses_healthy_session_and_sends_recovery(monkeypatch):
    recovery_calls = []
    monkeypatch.setattr(
        keeper_module,
        "load_keeper_config",
        lambda *_args, **_kwargs: ({"session": {}, "alert": {}}, None),
    )
    monkeypatch.setattr(
        keeper_module,
        "run_prepare_session",
        lambda *_args, **_kwargs: {"status": "reused"},
    )
    monkeypatch.setattr(
        keeper_module,
        "notify_recovery",
        lambda *_args, **_kwargs: recovery_calls.append(True) or {"sent": False},
    )
    monkeypatch.setattr(keeper_module, "current_flow_run_id", lambda: "flow-test")

    result = keeper_module.run_session_keeper("config/modules/session_keeper.json")

    assert result["status"] == "reused"
    assert recovery_calls == [True, True]


def test_keeper_alerts_after_two_login_attempts_fail(monkeypatch):
    alert_calls = []
    monkeypatch.setattr(
        keeper_module,
        "load_keeper_config",
        lambda *_args, **_kwargs: (
            {"session": {"required_stages": ["city_ops"]}, "alert": {}},
            None,
        ),
    )
    monkeypatch.setattr(
        keeper_module,
        "run_prepare_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SessionLoginError([RuntimeError("one"), RuntimeError("two")])
        ),
    )
    monkeypatch.setattr(
        keeper_module,
        "notify_failure",
        lambda *_args, **_kwargs: alert_calls.append(True) or {"sent": True},
    )
    monkeypatch.setattr(keeper_module, "current_flow_run_id", lambda: "flow-test")

    with pytest.raises(SessionLoginError):
        keeper_module.run_session_keeper("config/modules/session_keeper.json")

    assert alert_calls == [True]


def test_keeper_infrastructure_failure_never_requests_login(monkeypatch):
    alert_calls = []
    monkeypatch.setattr(
        keeper_module,
        "load_keeper_config",
        lambda *_args, **_kwargs: (
            {"session": {"required_stages": ["city_ops"]}, "alert": {}},
            None,
        ),
    )
    monkeypatch.setattr(
        keeper_module,
        "run_prepare_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SessionInfrastructureError("ConnectTimeout")
        ),
    )
    monkeypatch.setattr(
        keeper_module,
        "notify_failure",
        lambda *_args, **_kwargs: alert_calls.append(True) or {"sent": True},
    )
    monkeypatch.setattr(keeper_module, "current_flow_run_id", lambda: "flow-test")

    with pytest.raises(SessionInfrastructureError):
        keeper_module.run_session_keeper("config/modules/session_keeper.json")

    assert alert_calls == [True]


def test_prefect_retry_condition_retries_only_infrastructure_failures():
    class FakeState:
        def __init__(self, error):
            self.error = error

        def result(self):
            raise self.error

    assert (
        keeper_module.retry_infrastructure_only(
            None,
            None,
            FakeState(SessionInfrastructureError("ConnectTimeout")),
        )
        is True
    )
    assert (
        keeper_module.retry_infrastructure_only(
            None,
            None,
            FakeState(SessionLoginError([RuntimeError("one"), RuntimeError("two")])),
        )
        is False
    )
