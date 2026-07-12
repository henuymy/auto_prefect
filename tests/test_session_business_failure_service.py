import pytest

from services.session_business_failure_service import (
    build_business_session_incident,
    run_with_business_session_reporting,
)
from services.session_manager import SessionInfrastructureError, SessionLoginError


@pytest.mark.parametrize(
    ("failure", "category", "attempt_count"),
    [
        (SessionLoginError([RuntimeError("one"), RuntimeError("two")]), "authentication", 2),
        (SessionInfrastructureError("ConnectTimeout"), "infrastructure", 0),
    ],
)
def test_business_failure_uses_keeper_incident_key_and_trigger_source(failure, category, attempt_count):
    incident = build_business_session_incident(failure, trigger_source="auto-notify-flow", flow_run_id="flow-1")

    assert incident["incident_key"] == f"{category}:shared-session"
    assert incident["trigger_source"] == "auto-notify-flow"
    assert incident["attempt_count"] == attempt_count


def test_business_failure_wrapper_reports_and_reraises_classified_failure():
    failure = SessionInfrastructureError("ConnectTimeout")
    reported = []

    with pytest.raises(SessionInfrastructureError):
        run_with_business_session_reporting(
            lambda: (_ for _ in ()).throw(failure),
            trigger_source="auto-notify-flow",
            reporter=lambda exc, **kwargs: reported.append((exc, kwargs)),
        )

    assert reported == [(failure, {"trigger_source": "auto-notify-flow"})]


def test_business_failure_wrapper_ignores_unclassified_business_errors():
    reported = []

    with pytest.raises(RuntimeError, match="ordinary failure"):
        run_with_business_session_reporting(
            lambda: (_ for _ in ()).throw(RuntimeError("ordinary failure")),
            trigger_source="auto-notify-flow",
            reporter=lambda *args, **kwargs: reported.append((args, kwargs)),
        )

    assert reported == []
