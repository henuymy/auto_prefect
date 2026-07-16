from services.business_run_alert_service import (
    business_alert_state_path,
    notify_business_run_failure,
)


def incident(flow_run_id="run-1"):
    return {
        "incident_key": f"development:report:daily-report:{flow_run_id}",
        "workload_type": "report",
        "workload_id": "daily-report",
        "flow_name": "auto-notify-flow",
        "flow_run_name": "daily-run",
        "flow_run_id": flow_run_id,
        "failed_stage": "报表下载",
        "error_summary": "HTTP 500",
    }


def test_business_run_alert_sends_once_per_development_flow_run(tmp_path):
    messages = []
    config = {
        "environment": "development",
        "webhook_url": "https://example.invalid/webhook",
        "incident_state_path": str(tmp_path / "business-runs.json"),
    }

    def sender(_url, message, timeout=30):
        messages.append(message)
        return {"errcode": 0}

    assert notify_business_run_failure(config, incident(), sender=sender)["sent"] is True
    assert notify_business_run_failure(config, incident(), sender=sender)["suppressed"] is True
    assert len(messages) == 1
    assert "daily-report" in messages[0]
    assert "报表下载" in messages[0]


def test_business_run_alert_is_disabled_outside_development(tmp_path):
    config = {
        "environment": "production",
        "webhook_url": "https://example.invalid/webhook",
        "incident_state_path": str(tmp_path / "business-runs.json"),
    }

    result = notify_business_run_failure(config, incident(), sender=lambda *_args, **_kwargs: None)

    assert result == {"sent": False, "suppressed": True, "reason": "environment"}


def test_business_alert_state_is_written_under_external_runtime_root(monkeypatch, tmp_path):
    runtime_root = tmp_path / "shared-runtime"
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(runtime_root))

    assert business_alert_state_path(tmp_path / "project") == (
        runtime_root / "session" / "business-alerts" / "incident_state.json"
    ).resolve()
