from datetime import datetime
import importlib.util
from pathlib import Path
import sys


SPEC = importlib.util.spec_from_file_location(
    "session_lifetime_runner", Path(__file__).parents[1] / "session_lifetime_runner.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_format_console_event_reports_login_probe_and_schedule() -> None:
    timestamp = datetime(2026, 7, 12, 23, 30, 0).astimezone().isoformat()

    assert "[LOGIN]" in MODULE.format_console_event(
        {"event_type": "login_completed", "case": "headless", "timestamp": timestamp}
    )
    assert "result=valid" in MODULE.format_console_event(
        {
            "event_type": "aggregate_probe",
            "case": "headless",
            "probe_kind": "fixed",
            "classification": "valid",
        }
    )
    assert "[SCHEDULE]" in MODULE.format_console_event(
        {
            "event_type": "schedule_updated",
            "case": "headless",
            "next_probe_at": timestamp,
        }
    )


def test_probe_classification_marks_redirect_as_authentication_failure() -> None:
    assert (
        MODULE.classify_probe_result({"status_code": 302, "ok": False})
        == "authentication_failure"
    )


def test_probe_classification_keeps_server_error_as_infrastructure_failure() -> None:
    assert (
        MODULE.classify_probe_result({"status_code": 500, "ok": False})
        == "infrastructure_failure"
    )
