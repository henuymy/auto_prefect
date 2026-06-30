from __future__ import annotations

import pytest

from tasks import dashboard_tasks


def test_metric_task_dispatches_monthly_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(
        dashboard_tasks,
        "get_run_logger",
        lambda: object(),
    )
    monkeypatch.setattr(
        dashboard_tasks,
        "execute_dashboard_monthly_pipeline",
        lambda **kwargs: calls.append(kwargs) or {"status": "SUCCESS"},
    )

    result = dashboard_tasks.run_dashboard_metric_task.fn(
        mode="month",
        config_path="config/dashboard/session.json",
        trigger_type="MANUAL",
        force_refresh=True,
    )

    assert result == {"status": "SUCCESS"}
    assert calls[0]["trigger_type"] == "MANUAL"
    assert calls[0]["force_refresh"] is True


def test_metric_task_rejects_unknown_mode():
    with pytest.raises(ValueError, match="mode 只支持"):
        dashboard_tasks.run_dashboard_metric_task.fn(
            mode="UNKNOWN",
            config_path="config/dashboard/session.json",
            trigger_type="MANUAL",
        )


def test_metric_task_dispatches_v2_pipeline_from_config(monkeypatch):
    calls = []
    monkeypatch.setattr(
        dashboard_tasks,
        "load_dashboard_config",
        lambda path: ({"schema_version": 2}, path),
    )
    monkeypatch.setattr(dashboard_tasks, "get_run_logger", lambda: object())
    monkeypatch.setattr(
        dashboard_tasks,
        "execute_dashboard_v2_pipeline",
        lambda **kwargs: calls.append(kwargs) or {"status": "SUCCESS"},
    )

    result = dashboard_tasks.run_dashboard_metric_task.fn(
        mode="REALTIME",
        config_path="config/dashboard/session.json",
        trigger_type="MANUAL",
    )

    assert result == {"status": "SUCCESS"}
    assert calls[0]["period_type"] == "REALTIME"
