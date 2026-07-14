from services.browser_session import (
    format_browser_close_result,
    summarize_browser_close_result,
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
