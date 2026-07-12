import pytest

from services.session_retry_service import run_with_session_refresh_once


def test_business_step_refreshes_once_and_retries_only_failed_step():
    calls = []

    def operation():
        calls.append("operation")
        if calls.count("operation") == 1:
            raise RuntimeError("session expired: HTTP 302")
        return "ok"

    def refresh():
        calls.append("refresh")
        return {"status": "refreshed"}

    assert run_with_session_refresh_once(operation, refresh) == "ok"
    assert calls == ["operation", "refresh", "operation"]


def test_business_step_does_not_refresh_for_network_failure():
    refresh_calls = []

    with pytest.raises(RuntimeError, match="ConnectTimeout"):
        run_with_session_refresh_once(
            lambda: (_ for _ in ()).throw(RuntimeError("ConnectTimeout")),
            lambda: refresh_calls.append(True),
        )

    assert refresh_calls == []


def test_business_step_stops_after_refresh_and_second_auth_failure():
    refresh_calls = []

    with pytest.raises(RuntimeError, match="HTTP 302"):
        run_with_session_refresh_once(
            lambda: (_ for _ in ()).throw(RuntimeError("session expired: HTTP 302")),
            lambda: refresh_calls.append(True) or {"status": "refreshed"},
        )

    assert refresh_calls == [True]
