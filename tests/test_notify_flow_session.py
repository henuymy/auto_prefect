import pytest

from flows.notify_single_flow import (
    run_notify_download_with_session_refresh,
    run_notify_session_preparation,
)
from services.session_retry_service import RefreshBudget


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
