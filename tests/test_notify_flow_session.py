import json

import pytest

from flows.notify_single_flow import (
    prepare_notify_session,
    run_notify_download_with_session_refresh,
    run_notify_session_preparation,
)
from services.session_retry_service import RefreshBudget
from services.session_alert_service import notify_session_failure, notify_session_recovery
from services.session_business_failure_service import recover_business_session_incidents


@pytest.mark.parametrize("force_refresh", [False, True])
def test_notify_session_preparation_uses_one_full_login_attempt(monkeypatch, force_refresh):
    calls = []
    monkeypatch.setattr(
        "flows.notify_single_flow.prepare_session_task",
        lambda config, **kwargs: calls.append((config, kwargs)) or {"status": "refreshed"},
    )
    monkeypatch.setattr(
        "flows.notify_single_flow.run_notify_session_preparation",
        lambda operation: operation(),
    )

    config = {"name": "session"}
    result = prepare_notify_session(config, force_refresh=force_refresh)

    assert result == {"status": "refreshed"}
    assert calls == [
        (
            config,
            {"force_refresh": force_refresh, "login_attempts": 1},
        )
    ]


def test_notify_poll_iterations_share_one_refresh_budget():
    outcomes = iter([RuntimeError("session expired: HTTP 302"), "first-poll", RuntimeError("session expired: HTTP 302")])
    refresh_calls = []

    def operation():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    budget = RefreshBudget()
    assert run_notify_download_with_session_refresh(operation, lambda: refresh_calls.append(True) or {"status": "refreshed"}, budget) == "first-poll"
    with pytest.raises(RuntimeError, match="HTTP 302"):
        run_notify_download_with_session_refresh(operation, lambda: refresh_calls.append(True) or {"status": "refreshed"}, budget)

    assert refresh_calls == [True]


def test_notify_initial_forced_mode_disables_later_active_refresh():
    refresh_calls = []

    with pytest.raises(RuntimeError, match="HTTP 302"):
        run_notify_download_with_session_refresh(
            lambda: (_ for _ in ()).throw(RuntimeError("session expired: HTTP 302")),
            lambda: refresh_calls.append(True) or {"status": "refreshed"},
            RefreshBudget(consumed=True),
        )

    assert refresh_calls == []


def test_notify_reports_second_auth_failure_after_successful_refresh():
    second_failure = RuntimeError("session expired: HTTP 403")
    outcomes = iter([RuntimeError("session expired: HTTP 302"), second_failure])
    refresh_calls = []
    reported = []

    def operation():
        outcome = next(outcomes)
        raise outcome

    with pytest.raises(RuntimeError) as exc_info:
        run_notify_download_with_session_refresh(
            operation,
            lambda: refresh_calls.append(True) or {"status": "refreshed"},
            RefreshBudget(),
            reporter=lambda exc, **kwargs: reported.append((exc, kwargs)),
        )

    assert exc_info.value is second_failure
    assert refresh_calls == [True]
    assert reported == [(second_failure, {"trigger_source": "auto-notify-flow"})]


def test_notify_initial_forced_mode_reports_auth_failure_without_refresh():
    failure = RuntimeError("session expired: HTTP 401")
    refresh_calls = []
    reported = []

    with pytest.raises(RuntimeError) as exc_info:
        run_notify_download_with_session_refresh(
            lambda: (_ for _ in ()).throw(failure),
            lambda: refresh_calls.append(True) or {"status": "refreshed"},
            RefreshBudget(consumed=True),
            reporter=lambda exc, **kwargs: reported.append((exc, kwargs)),
        )

    assert exc_info.value is failure
    assert refresh_calls == []
    assert reported == [(failure, {"trigger_source": "auto-notify-flow"})]


def test_notify_successful_preparation_runs_shared_recovery():
    recoveries = []

    result = run_notify_session_preparation(
        lambda: {"status": "refreshed"},
        recoverer=lambda **kwargs: recoveries.append(kwargs),
    )

    assert result == {"status": "refreshed"}
    assert recoveries == [{"trigger_source": "auto-notify-flow"}]


def test_notify_invalid_preparation_does_not_recover_or_clear_active_incident(tmp_path):
    alert_config = {
        "webhook_url": "https://example.invalid/webhook",
        "incident_state_path": str(tmp_path / "incident.json"),
    }
    messages = []

    def sender(_url, text, timeout=30):
        messages.append(text)
        return {"errcode": 0}

    notify_session_failure(
        alert_config,
        {
            "incident_key": "authentication:shared-session",
            "trigger_source": "auto-notify-flow",
            "failure_category": "authentication",
            "failed_stages": ["shared-session"],
            "attempt_count": 2,
            "errors": ["login failed"],
            "flow_run_id": "failure-flow",
            "next_scheduled_at": "later",
        },
        sender=sender,
    )

    def recoverer(**_kwargs):
        return recover_business_session_incidents(
            alert_config,
            flow_run_id="invalid-flow",
            notifier=lambda config, recovery, **kwargs: notify_session_recovery(
                config,
                recovery,
                sender=sender,
                **kwargs,
            ),
        )

    with pytest.raises(RuntimeError, match="会话不可用: login_disabled"):
        run_notify_session_preparation(
            lambda: {"status": "invalid", "reason": "login_disabled"},
            recoverer=recoverer,
        )

    state = json.loads((tmp_path / "incident.json").read_text(encoding="utf-8"))
    assert state["active_incident_key"] == "authentication:shared-session"
    assert not any(message.startswith("[自动登录恢复]") for message in messages)


def test_notify_unknown_preparation_status_does_not_send_recovery():
    recoveries = []

    result = run_notify_session_preparation(
        lambda: {"status": "pending"},
        recoverer=lambda **kwargs: recoveries.append(kwargs),
    )

    assert result == {"status": "pending"}
    assert recoveries == []
