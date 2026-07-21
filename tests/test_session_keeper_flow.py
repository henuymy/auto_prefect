import pytest

from flows import session_keeper_flow as keeper_module
from services.session_manager import SessionLoginError


def test_format_session_health_confirmation_lists_prepared_stages():
    assert keeper_module.format_session_health_confirmation(
        {
            "status": "reused",
            "stages": ["report_analysis", "smart_ops", "city_ops", "data_market"],
        }
    ) == (
        "共享会话健康状态确认: report_analysis=healthy, smart_ops=healthy, "
        "city_ops=healthy, data_market=healthy"
    )


def test_keeper_logs_session_health_confirmation(monkeypatch):
    messages = []

    class Logger:
        def info(self, message):
            messages.append(message)

    monkeypatch.setattr(keeper_module, "get_run_logger", Logger)
    monkeypatch.setattr(
        keeper_module,
        "run_session_keeper",
        lambda *_args: {
            "status": "reused",
            "stages": ["report_analysis", "smart_ops", "city_ops", "data_market"],
        },
    )

    keeper_module.session_keeper_flow.fn()

    assert messages == [
        "检查共享登录会话",
        "共享会话健康状态确认: report_analysis=healthy, smart_ops=healthy, "
        "city_ops=healthy, data_market=healthy",
    ]


def test_keeper_reuses_healthy_session_without_notification(monkeypatch):
    monkeypatch.setattr(
        keeper_module,
        "load_keeper_config",
        lambda *_args, **_kwargs: ({"session": {}}, None),
    )
    monkeypatch.setattr(keeper_module, "run_prepare_session", lambda *_args: {"status": "reused"})

    assert keeper_module.run_session_keeper() == {"status": "reused"}


def test_keeper_routes_failures_and_recovery_through_shared_session_reporting(monkeypatch):
    calls = []

    class Logger:
        def info(self, *_args):
            pass

    def reporting_wrapper(operation, **kwargs):
        calls.append(kwargs)
        return operation()

    monkeypatch.setattr(keeper_module, "get_run_logger", Logger)
    monkeypatch.setattr(keeper_module, "run_with_business_session_reporting", reporting_wrapper)
    monkeypatch.setattr(keeper_module, "run_session_keeper", lambda *_args: {"status": "reused"})

    assert keeper_module.session_keeper_flow.fn() == {"status": "reused"}
    assert calls == [
        {
            "trigger_source": "session-keeper",
            "flow_name": "session-keeper-flow",
            "affected_stage": "共享会话",
            "recover_on_success": True,
        }
    ]


def test_keeper_reraises_failure_without_notification(monkeypatch):
    monkeypatch.setattr(
        keeper_module,
        "load_keeper_config",
        lambda *_args, **_kwargs: ({"session": {}}, None),
    )
    monkeypatch.setattr(
        keeper_module,
        "run_prepare_session",
        lambda *_args: (_ for _ in ()).throw(SessionLoginError([RuntimeError("one")])),
    )

    with pytest.raises(SessionLoginError):
        keeper_module.run_session_keeper()


def test_keeper_uses_one_prefect_attempt_and_one_browser_login(monkeypatch):
    options = {}
    calls = []

    class FakePrepareTask:
        def with_options(self, **kwargs):
            options.update(kwargs)
            return self

        def __call__(self, config, **kwargs):
            calls.append((config, kwargs))
            return {"status": "refreshed"}

    monkeypatch.setattr(keeper_module, "prepare_session_task", FakePrepareTask())

    config = {"required_stages": ["city_ops"]}
    assert keeper_module.run_prepare_session(config) == {"status": "refreshed"}
    assert options == {"retries": 0}
    assert calls == [(config, {"force_refresh": False, "login_attempts": 1})]
