from __future__ import annotations

from backend.services import prefect_runner


class _Completed:
    returncode = 0
    stdout = "ok"
    stderr = ""


def _patch_publish(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(prefect_runner, "PROJECT_ROOT", tmp_path)
    task_path = tmp_path / "config" / "tasks" / "task.json"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(prefect_runner, "write_task_config", lambda *args, **kwargs: task_path)

    def fake_run(command, **kwargs):
        commands.append(command)
        return _Completed()

    monkeypatch.setattr(prefect_runner.subprocess, "run", fake_run)
    return commands


def test_publish_without_cron_does_not_change_schedule(monkeypatch, tmp_path):
    commands = _patch_publish(monkeypatch, tmp_path)

    result = prefect_runner.publish_config({"name": "日报", "enabled": False, "deployment": {"cron": "", "timezone": "Asia/Shanghai"}})

    assert len(commands) == 1
    assert "--cron" not in commands[0]
    assert result["scheduleStatus"] == "none"


def test_publish_with_enabled_schedule_resumes(monkeypatch, tmp_path):
    commands = _patch_publish(monkeypatch, tmp_path)

    result = prefect_runner.publish_config({"name": "日报", "enabled": True, "deployment": {"cron": "0 9 * * *", "timezone": "Asia/Shanghai"}})

    assert len(commands) == 2
    assert commands[0][commands[0].index("--cron") + 1] == "0 9 * * *"
    assert commands[1][3:6] == ["deployment", "schedule", "resume"]
    assert commands[1][-2:] == ["auto-notify-flow/notify-日报", "--all"]
    assert result["scheduleStatus"] == "enabled"


def test_publish_with_disabled_schedule_pauses(monkeypatch, tmp_path):
    commands = _patch_publish(monkeypatch, tmp_path)

    result = prefect_runner.publish_config({"name": "日报", "enabled": False, "deployment": {"cron": "0 9 * * *", "timezone": "Asia/Shanghai"}})

    assert len(commands) == 2
    assert commands[0][commands[0].index("--cron") + 1] == "0 9 * * *"
    assert commands[1][3:6] == ["deployment", "schedule", "pause"]
    assert commands[1][-2:] == ["auto-notify-flow/notify-日报", "--all"]
    assert result["scheduleStatus"] == "disabled"
