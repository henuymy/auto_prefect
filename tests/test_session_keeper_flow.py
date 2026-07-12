from datetime import datetime, timedelta, timezone

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


def test_keeper_alerts_on_infrastructure_failure(monkeypatch):
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


@pytest.mark.parametrize(
    "failure",
    [
        SessionLoginError([RuntimeError("one"), RuntimeError("two")]),
        SessionInfrastructureError("ConnectTimeout"),
    ],
)
def test_keeper_schedules_next_run_from_failure_time_after_quarter_hour(
    monkeypatch,
    failure,
):
    local_timezone = timezone(timedelta(hours=8))
    before_failure = datetime(2026, 7, 12, 10, 14, 59, tzinfo=local_timezone)
    after_failure = datetime(2026, 7, 12, 10, 15, 1, tzinfo=local_timezone)
    clock = {"now": before_failure}
    incidents = []

    class FakeDatetime:
        @classmethod
        def now(cls):
            return clock["now"]

    def fail_after_crossing_quarter_hour(*_args, **_kwargs):
        clock["now"] = after_failure
        raise failure

    monkeypatch.setattr(
        keeper_module,
        "load_keeper_config",
        lambda *_args, **_kwargs: (
            {"session": {"required_stages": ["city_ops"]}, "alert": {}},
            None,
        ),
    )
    monkeypatch.setattr(keeper_module, "datetime", FakeDatetime)
    monkeypatch.setattr(
        keeper_module,
        "run_prepare_session",
        fail_after_crossing_quarter_hour,
    )
    monkeypatch.setattr(
        keeper_module,
        "notify_failure",
        lambda _config, incident: incidents.append(incident) or {"sent": True},
    )
    monkeypatch.setattr(keeper_module, "current_flow_run_id", lambda: "flow-test")

    with pytest.raises(type(failure)):
        keeper_module.run_session_keeper("config/modules/session_keeper.json")

    assert incidents[0]["next_scheduled_at"] == "2026-07-12T10:30:00+08:00"


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
