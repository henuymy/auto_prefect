from pathlib import Path
from types import SimpleNamespace

from services import browser_session
from services.browser_session import (
    close_browser_session,
    format_browser_close_result,
    summarize_browser_close_result,
    write_session_state,
)


def test_browser_close_summary_excludes_process_ids_and_local_paths():
    result = {
        "status": "closed",
        "stopped_pids": [15880, 8170],
        "remaining_pids": [19158],
        "state_path": r"C:\\AutoNotifyRuntime\\session\\browser-session.json",
        "user_data_dir": r"C:\\AutoNotifyRuntime\\session\\browser-profile",
    }

    assert summarize_browser_close_result(result) == {
        "status": "closed",
        "stopped_count": 2,
        "remaining_count": 1,
    }
    assert format_browser_close_result(result) == (
        "status=closed，stopped_count=2，remaining_count=1"
    )


def test_stop_process_ids_reports_only_successful_taskkill_calls(monkeypatch):
    outcomes = iter(
        [
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=1),
        ]
    )
    monkeypatch.setattr(browser_session.subprocess, "run", lambda *_args, **_kwargs: next(outcomes))

    result = browser_session.stop_process_ids([102, 101])

    assert result == {"stopped_pids": [101], "failed_pids": [102]}


def test_close_browser_session_preserves_state_when_process_inspection_is_unavailable(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "browser-session.json"
    profile_dir = tmp_path / "browser-profile"
    write_session_state(
        state_path,
        {"user_data_dir": str(profile_dir), "browser_pids": [101]},
    )
    monkeypatch.setattr(
        browser_session,
        "inspect_edge_processes_by_user_data_dir",
        lambda _profile: {"available": False, "pids": []},
        raising=False,
    )

    result = close_browser_session(
        state_path,
        user_data_dir=profile_dir,
        wait_seconds=0,
    )

    assert result["status"] == "cleanup_unverified"
    assert state_path.exists()


def test_close_browser_session_waits_for_profile_process_exit_before_removing_state(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "browser-session.json"
    profile_dir = tmp_path / "browser-profile"
    write_session_state(
        state_path,
        {"user_data_dir": str(profile_dir), "browser_pids": [101]},
    )
    inspections = iter(
        [
            {"available": True, "pids": [101]},
            {"available": True, "pids": [101]},
            {"available": True, "pids": []},
        ]
    )
    stop_calls = []
    waits = []
    monkeypatch.setattr(
        browser_session,
        "inspect_edge_processes_by_user_data_dir",
        lambda _profile: next(inspections),
        raising=False,
    )
    monkeypatch.setattr(
        browser_session,
        "stop_process_ids",
        lambda pids: stop_calls.append(pids) or {"stopped_pids": list(pids), "failed_pids": []},
    )
    monkeypatch.setattr(browser_session.time, "sleep", waits.append)

    result = close_browser_session(
        state_path,
        user_data_dir=profile_dir,
        wait_seconds=1,
        poll_seconds=0.25,
    )

    assert result["status"] == "closed"
    assert stop_calls == [[101], [101]]
    assert waits == [0.25]
    assert not Path(state_path).exists()
