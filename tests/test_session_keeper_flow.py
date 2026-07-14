import pytest

from flows import session_keeper_flow as keeper_module
from services.session_manager import SessionLoginError


def test_keeper_reuses_healthy_session_without_notification(monkeypatch):
    monkeypatch.setattr(
        keeper_module,
        "load_keeper_config",
        lambda *_args, **_kwargs: ({"session": {}}, None),
    )
    monkeypatch.setattr(keeper_module, "run_prepare_session", lambda *_args: {"status": "reused"})

    assert keeper_module.run_session_keeper() == {"status": "reused"}


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
