import json
from pathlib import Path

from services.session_alert_service import (
    notify_session_failure,
    notify_session_recovery,
)


def alert_config(tmp_path: Path):
    return {
        "webhook_url": "https://example.invalid/webhook",
        "incident_state_path": str(tmp_path / "incident_state.json"),
        "host_name": "windows-worker-1",
    }


def test_failure_alert_is_sent_once_for_same_incident(tmp_path):
    messages = []
    config = alert_config(tmp_path)
    incident = {
        "incident_key": "authentication:city_ops",
        "trigger_source": "session-keeper",
        "failure_category": "authentication",
        "failed_stages": ["city_ops"],
        "attempt_count": 2,
        "errors": ["first failure", "second failure"],
        "flow_run_id": "flow-123",
        "next_scheduled_at": "2026-07-12T16:30:00+08:00",
    }

    first = notify_session_failure(
        config,
        incident,
        sender=lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0},
    )
    second = notify_session_failure(
        config,
        incident,
        sender=lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0},
    )

    assert first["sent"] is True
    assert second["suppressed"] is True
    assert len(messages) == 1
    assert "flow-123" in messages[0]


def test_alert_text_never_contains_authentication_secrets(tmp_path):
    messages = []
    config = alert_config(tmp_path)
    incident = {
        "incident_key": "authentication:report_analysis",
        "trigger_source": "business-flow",
        "failure_category": "authentication",
        "failed_stages": ["report_analysis"],
        "attempt_count": 2,
        "errors": ["Cookie=raw-cookie Token=raw-token password=raw-password"],
        "flow_run_id": "flow-456",
        "next_scheduled_at": "2026-07-12T16:45:00+08:00",
    }

    notify_session_failure(
        config,
        incident,
        sender=lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0},
    )

    assert "raw-cookie" not in messages[0]
    assert "raw-token" not in messages[0]
    assert "raw-password" not in messages[0]
    persisted_state = json.loads(Path(config["incident_state_path"]).read_text(encoding="utf-8"))
    serialized_state = json.dumps(persisted_state, ensure_ascii=False)
    assert "raw-cookie" not in serialized_state
    assert "raw-token" not in serialized_state
    assert "raw-password" not in serialized_state


def test_recovery_message_is_sent_once_and_clears_active_incident(tmp_path):
    messages = []
    config = alert_config(tmp_path)
    incident = {
        "incident_key": "authentication:city_ops",
        "trigger_source": "session-keeper",
        "failure_category": "authentication",
        "failed_stages": ["city_ops"],
        "attempt_count": 2,
        "errors": ["failure"],
        "flow_run_id": "flow-123",
        "next_scheduled_at": "2026-07-12T16:30:00+08:00",
    }
    sender = lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0}
    notify_session_failure(config, incident, sender=sender)

    first = notify_session_recovery(
        config,
        {"incident_key": incident["incident_key"], "flow_run_id": "flow-789"},
        sender=sender,
    )
    second = notify_session_recovery(
        config,
        {"incident_key": incident["incident_key"], "flow_run_id": "flow-790"},
        sender=sender,
    )

    assert first["sent"] is True
    assert second["suppressed"] is True
    assert len(messages) == 2
