import json
import multiprocessing
import time
from threading import Event
from pathlib import Path

import pytest

from services import session_alert_service
from services.session_alert_service import (
    dispatch_session_notification,
    notify_session_failure,
    notify_session_recovery,
)


def test_resolve_rebases_logical_runtime_paths(monkeypatch, tmp_path):
    runtime_root = tmp_path / "shared-runtime"
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(runtime_root))

    assert session_alert_service._resolve(
        "session/session-alerts/incident_state.json",
        tmp_path / "project",
    ) == (
        runtime_root / "session" / "session-alerts" / "incident_state.json"
    ).resolve()


def test_async_dispatch_does_not_wait_for_wecom_delivery():
    started = Event()
    release = Event()

    def notification():
        started.set()
        release.wait(timeout=2)

    started_at = time.monotonic()
    thread = dispatch_session_notification(notification, notification_type="failure")

    assert time.monotonic() - started_at < 0.1
    assert started.wait(timeout=1)
    assert thread.is_alive()
    release.set()
    thread.join(timeout=1)


def alert_config(tmp_path: Path):
    return {
        "webhook_url": "https://example.invalid/webhook",
        "incident_state_path": str(tmp_path / "incident_state.json"),
        "host_name": "windows-worker-1",
    }


def _failure_worker(config, incident, messages, results, start_event):
    start_event.wait(timeout=10)

    def sender(_url, text, timeout=30):
        messages.append(text)
        time.sleep(0.2)
        return {"errcode": 0}

    results.append(notify_session_failure(config, incident, sender=sender))


def _recovery_worker(config, recovery, messages, results, start_event):
    start_event.wait(timeout=10)

    def sender(_url, text, timeout=30):
        messages.append(text)
        time.sleep(0.2)
        return {"errcode": 0}

    results.append(notify_session_recovery(config, recovery, sender=sender))


def _run_concurrently(worker, config, payload, messages, results):
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    processes = [
        context.Process(
            target=worker,
            args=(config, payload, messages, results, start_event),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0


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
    assert "判定依据:" in messages[0]
    assert "Flow: 未提供" in messages[0]


def test_concurrent_failure_alert_is_one_cross_process_transaction(tmp_path):
    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager:
        messages = manager.list()
        results = manager.list()
        config = alert_config(tmp_path)
        incident = {
            "incident_key": "authentication:city_ops",
            "trigger_source": "session-keeper",
            "failure_category": "authentication",
            "failed_stages": ["city_ops"],
            "attempt_count": 2,
            "errors": ["failure"],
            "flow_run_id": "flow-concurrent",
            "next_scheduled_at": "2026-07-12T16:30:00+08:00",
        }

        _run_concurrently(_failure_worker, config, incident, messages, results)

        assert len(messages) == 1
        assert sorted(result["sent"] for result in results) == [False, True]


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


def test_alert_redacts_storage_credentials_and_bare_wecom_webhook(tmp_path):
    messages = []
    config = alert_config(tmp_path)
    bare_webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=raw-wecom-key"
    incident = {
        "incident_key": "authentication:report_analysis",
        "trigger_source": "business-flow",
        "failure_category": "authentication",
        "failed_stages": ["report_analysis"],
        "attempt_count": 2,
        "errors": [
            "Storage=raw-storage sessionStorage=raw-session-storage "
            "localStorage=raw-local-storage username=raw-user "
            "credentials=raw-credentials secret=raw-secret "
            "Authorization=Bearer raw-authorization "
            "JSESSIONID=raw-jsession accessToken=raw-access-token "
            f"uapToken=raw-uap-token {bare_webhook}"
        ],
        "flow_run_id": "flow-credentials",
        "next_scheduled_at": "2026-07-12T16:45:00+08:00",
    }

    notify_session_failure(
        config,
        incident,
        sender=lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0},
    )

    for sentinel in (
        "raw-storage",
        "raw-session-storage",
        "raw-local-storage",
        "raw-user",
        "raw-credentials",
        "raw-secret",
        "raw-authorization",
        "raw-jsession",
        "raw-access-token",
        "raw-uap-token",
        "raw-wecom-key",
    ):
        assert sentinel not in messages[0]


def test_alert_removes_compound_cookie_and_structured_storage_details(tmp_path):
    messages = []
    config = alert_config(tmp_path)
    incident = {
        "incident_key": "authentication:report_analysis",
        "trigger_source": "business-flow",
        "failure_category": "authentication",
        "failed_stages": ["report_analysis"],
        "attempt_count": 2,
        "errors": [
            "Cookie: foo=one; bar=raw-two",
            'localStorage={"profile":"raw-storage-value"}',
        ],
        "flow_run_id": "flow-structured-secret",
        "next_scheduled_at": "2026-07-12T16:45:00+08:00",
    }

    notify_session_failure(
        config,
        incident,
        sender=lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0},
    )

    assert messages[0].count("<redacted sensitive detail>") >= 2
    for leaked_fragment in (
        "Cookie",
        "foo=one",
        "bar=raw-two",
        "localStorage",
        "profile",
        "raw-storage-value",
        '{"profile"',
    ):
        assert leaked_fragment not in messages[0]


def test_alert_redacts_snake_case_and_hyphenated_sensitive_labels(tmp_path):
    messages = []
    config = alert_config(tmp_path)
    sensitive_details = [
        "access_token=raw-access-underscore",
        "refresh_token=raw-refresh-underscore",
        "storage_state=raw-storage-state",
        "session_storage=raw-session-storage-state",
        "cookie_header=foo=one; raw-cookie-header=two",
        "refresh-token=raw-refresh-hyphen",
    ]
    incident = {
        "incident_key": "authentication:report_analysis",
        "trigger_source": "business-flow",
        "failure_category": "authentication",
        "failed_stages": ["report_analysis"],
        "attempt_count": 2,
        "errors": sensitive_details,
        "flow_run_id": "flow-label-variants",
        "next_scheduled_at": "2026-07-12T16:45:00+08:00",
    }

    notify_session_failure(
        config,
        incident,
        sender=lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0},
    )

    assert messages[0].count("<redacted sensitive detail>") == len(sensitive_details)
    for secret_value in (
        "raw-access-underscore",
        "raw-refresh-underscore",
        "raw-storage-state",
        "raw-session-storage-state",
        "raw-cookie-header",
        "raw-refresh-hyphen",
    ):
        assert secret_value not in messages[0]


def test_alert_redacts_chinese_username_label_and_raw_stdout_secret(tmp_path):
    messages = []
    config = alert_config(tmp_path)
    incident = {
        "incident_key": "authentication:shared-session",
        "trigger_source": "auto-notify-flow",
        "failure_category": "authentication",
        "failed_stages": ["city_ops"],
        "attempt_count": 2,
        "errors": ["用户名: sentinel-user", "STDOUT: raw-stdout-secret"],
        "flow_run_id": "flow-secret",
        "next_scheduled_at": "later",
    }

    notify_session_failure(
        config,
        incident,
        sender=lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0},
    )

    assert "sentinel-user" not in messages[0]
    assert "raw-stdout-secret" not in messages[0]


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
    def sender(_url, text, timeout=30):
        messages.append(text)
        return {"errcode": 0}
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


def test_concurrent_recovery_is_one_cross_process_transaction(tmp_path):
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
    notify_session_failure(config, incident, sender=lambda *_args, **_kwargs: {"errcode": 0})

    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager:
        messages = manager.list()
        results = manager.list()
        _run_concurrently(
            _recovery_worker,
            config,
            {"incident_key": incident["incident_key"], "flow_run_id": "flow-recovery"},
            messages,
            results,
        )

        assert len(messages) == 1
        assert sorted(result["sent"] for result in results) == [False, True]


def test_sender_failure_does_not_persist_suppression_state(tmp_path):
    messages = []
    config = alert_config(tmp_path)
    incident = {
        "incident_key": "authentication:city_ops",
        "trigger_source": "session-keeper",
        "failure_category": "authentication",
        "failed_stages": ["city_ops"],
        "attempt_count": 2,
        "errors": ["failure"],
        "flow_run_id": "flow-failure",
        "next_scheduled_at": "2026-07-12T16:30:00+08:00",
    }

    def failing_sender(_url, _text, timeout=30):
        raise RuntimeError("send failed")

    try:
        notify_session_failure(config, incident, sender=failing_sender)
    except RuntimeError as exc:
        assert str(exc) == "send failed"
    else:
        raise AssertionError("sender failure must propagate")

    assert not Path(config["incident_state_path"]).exists()
    result = notify_session_failure(
        config,
        incident,
        sender=lambda _url, text, timeout=30: messages.append(text) or {"errcode": 0},
    )
    assert result["sent"] is True
    assert len(messages) == 1


def test_nonzero_wecom_response_does_not_suppress_failure_retry(tmp_path):
    config = alert_config(tmp_path)
    incident = {
        "incident_key": "authentication:city_ops",
        "trigger_source": "session-keeper",
        "failure_category": "authentication",
        "failed_stages": ["city_ops"],
        "attempt_count": 2,
        "errors": ["failure"],
        "flow_run_id": "flow-wecom-error",
        "next_scheduled_at": "2026-07-12T16:30:00+08:00",
    }

    with pytest.raises(RuntimeError, match="WeCom session alert send failed"):
        notify_session_failure(
            config,
            incident,
            sender=lambda *_args, **_kwargs: {"errcode": 93000, "errmsg": "sensitive detail"},
        )

    assert not Path(config["incident_state_path"]).exists()
    retry = notify_session_failure(
        config,
        incident,
        sender=lambda *_args, **_kwargs: {"errcode": 0},
    )
    assert retry["sent"] is True


def test_nonzero_wecom_response_does_not_suppress_recovery_retry(tmp_path):
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
    notify_session_failure(config, incident, sender=lambda *_args, **_kwargs: {"errcode": 0})
    recovery = {"incident_key": incident["incident_key"], "flow_run_id": "flow-recovery"}

    with pytest.raises(RuntimeError, match="WeCom session alert send failed"):
        notify_session_recovery(
            config,
            recovery,
            sender=lambda *_args, **_kwargs: {"errcode": 93000, "errmsg": "sensitive detail"},
        )

    state = json.loads(Path(config["incident_state_path"]).read_text(encoding="utf-8"))
    assert state["active_incident_key"] == incident["incident_key"]
    retry = notify_session_recovery(
        config,
        recovery,
        sender=lambda *_args, **_kwargs: {"errcode": 0},
    )
    assert retry["sent"] is True
