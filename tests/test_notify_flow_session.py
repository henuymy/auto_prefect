import pytest

from flows.notify_single_flow import run_notify_download_with_session_refresh
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
