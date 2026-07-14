import pytest

from services.session_business_failure_service import (
    build_business_session_incident,
    recover_business_session_incidents,
    run_with_business_session_reporting,
)
from services.session_alert_service import notify_session_failure, notify_session_recovery
from services.session_manager import SessionInfrastructureError, SessionLoginError


def run_notification_now(notification, **_kwargs):
    return notification()


@pytest.mark.parametrize(
    ("failure", "category", "attempt_count"),
    [
        (SessionLoginError([RuntimeError("one"), RuntimeError("two")]), "authentication", 2),
        (SessionInfrastructureError("ConnectTimeout"), "infrastructure", 0),
    ],
)
def test_business_failure_captures_actual_flow_context(failure, category, attempt_count):
    incident = build_business_session_incident(
        failure,
        trigger_source="auto-notify-flow",
        flow_name="auto-notify-flow",
        flow_run_name="nightly-20260714",
        flow_run_id="flow-1",
        affected_stage="报表下载",
    )

    assert incident["incident_key"] == f"{category}:shared-session"
    assert incident["trigger_source"] == "auto-notify-flow"
    assert incident["attempt_count"] == attempt_count
    assert "原始判定" in incident["failure_reason"]
    assert incident["flow_name"] == "auto-notify-flow"
    assert incident["flow_run_name"] == "nightly-20260714"
    assert incident["failed_stages"] == ["报表下载"]


def test_business_failure_wrapper_reports_and_reraises_classified_failure():
    failure = SessionInfrastructureError("ConnectTimeout")
    reported = []

    with pytest.raises(SessionInfrastructureError):
        run_with_business_session_reporting(
            lambda: (_ for _ in ()).throw(failure),
            trigger_source="auto-notify-flow",
            flow_name="auto-notify-flow",
            affected_stage="报表下载",
            reporter=lambda exc, **kwargs: reported.append((exc, kwargs)),
            notification_dispatcher=run_notification_now,
        )

    assert reported[0][0] is failure
    assert reported[0][1]["flow_name"] == "auto-notify-flow"
    assert reported[0][1]["affected_stage"] == "报表下载"


def test_business_failure_wrapper_ignores_unclassified_business_errors():
    reported = []

    with pytest.raises(RuntimeError, match="ordinary failure"):
        run_with_business_session_reporting(
            lambda: (_ for _ in ()).throw(RuntimeError("ordinary failure")),
            trigger_source="auto-notify-flow",
            flow_name="auto-notify-flow",
            affected_stage="报表下载",
            reporter=lambda *args, **kwargs: reported.append((args, kwargs)),
        )

    assert reported == []


def test_business_failure_wrapper_reports_exhausted_auth_runtime_and_preserves_it():
    failure = RuntimeError("session expired: HTTP 401")
    reported = []

    with pytest.raises(RuntimeError) as exc_info:
        run_with_business_session_reporting(
            lambda: (_ for _ in ()).throw(failure),
            trigger_source="auto-notify-flow",
            flow_name="auto-notify-flow",
            affected_stage="报表下载",
            reporter=lambda exc, **kwargs: reported.append((exc, kwargs)),
            notification_dispatcher=run_notification_now,
        )

    assert exc_info.value is failure
    assert reported[0][0] is failure
    assert build_business_session_incident(
        failure,
        trigger_source="auto-notify-flow",
        flow_name="auto-notify-flow",
        flow_run_name="manual",
        flow_run_id="flow-runtime",
        affected_stage="报表下载",
    )["incident_key"] == "authentication:shared-session"


def test_business_failure_wrapper_does_not_treat_non_login_redirect_as_authentication_failure():
    reported = []
    failure = RuntimeError("下载接口返回重定向但不是明确登录地址: HTTP 302 GET")

    with pytest.raises(RuntimeError) as exc_info:
        run_with_business_session_reporting(
            lambda: (_ for _ in ()).throw(failure),
            trigger_source="auto-notify-flow",
            flow_name="auto-notify-flow",
            affected_stage="报表下载",
            reporter=lambda *args, **kwargs: reported.append((args, kwargs)),
        )

    assert exc_info.value is failure
    assert reported == []


def test_alert_delivery_failure_does_not_replace_primary_session_failure():
    failure = SessionInfrastructureError("ConnectTimeout")

    with pytest.raises(SessionInfrastructureError) as exc_info:
        run_with_business_session_reporting(
            lambda: (_ for _ in ()).throw(failure),
            trigger_source="dashboard-metric-flow",
            flow_name="dashboard-metric-flow",
            affected_stage="驾驶舱指标采集",
            reporter=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("webhook secret delivery failure")
            ),
            notification_dispatcher=run_notification_now,
        )

    assert exc_info.value is failure


def test_successful_business_operation_runs_shared_recovery_for_both_keys():
    recovered = []

    result = run_with_business_session_reporting(
        lambda: "ok",
        trigger_source="auto-notify-flow",
        flow_name="auto-notify-flow",
        affected_stage="会话准备",
        recover_on_success=True,
        recoverer=lambda **kwargs: recovered.append(kwargs),
        notification_dispatcher=run_notification_now,
    )

    assert result == "ok"
    assert recovered[0]["trigger_source"] == "auto-notify-flow"
    assert recovered[0]["flow_name"] == "auto-notify-flow"


def test_business_failure_recovery_allows_later_failure_alert(tmp_path):
    config = {
        "webhook_url": "https://example.invalid/webhook",
        "incident_state_path": str(tmp_path / "incident.json"),
    }
    messages = []

    def sender(_url, text, timeout=30):
        messages.append(text)
        return {"errcode": 0}

    failure = SessionLoginError([RuntimeError("login failed")])
    incident = build_business_session_incident(
        failure,
        trigger_source="auto-notify-flow",
        flow_name="auto-notify-flow",
        flow_run_name="failure-run",
        flow_run_id="flow-failure-1",
        affected_stage="报表下载",
    )

    first = notify_session_failure(config, incident, sender=sender)
    recover_business_session_incidents(
        config,
        recovery={
            "trigger_source": "auto-notify-flow",
            "flow_name": "auto-notify-flow",
            "flow_run_name": "recovery-run",
            "flow_run_id": "flow-recovery",
        },
        notifier=lambda alert_config, recovery, **kwargs: notify_session_recovery(
            alert_config,
            recovery,
            sender=sender,
            **kwargs,
        ),
    )
    second = notify_session_failure(
        config,
        {**incident, "flow_run_id": "flow-failure-2"},
        sender=sender,
    )

    assert first["sent"] is True
    assert second["sent"] is True
    assert sum(message.startswith("[共享会话故障告警]") for message in messages) == 2
    assert sum(message.startswith("[共享会话恢复通知]") for message in messages) == 1
