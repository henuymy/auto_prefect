from __future__ import annotations

from backend.services import prefect_runner


class _Completed:
    def __init__(self, stdout="ok", returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_publish(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(prefect_runner, "PROJECT_ROOT", tmp_path)
    task_path = tmp_path / "config" / "tasks" / "task.json"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(prefect_runner, "write_task_config", lambda *args, **kwargs: task_path)

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[3:6] == ["deployment", "schedule", "ls"]:
            return _Completed(stdout='[{"id":"schedule-1"}]')
        return _Completed()

    monkeypatch.setattr(prefect_runner.subprocess, "run", fake_run)
    return commands


def test_publish_without_crons_removes_existing_schedules(monkeypatch, tmp_path):
    commands = _patch_publish(monkeypatch, tmp_path)

    result = prefect_runner.publish_config({"name": "日报", "enabled": False, "deployment": {"crons": [], "timezone": "Asia/Shanghai"}})

    assert len(commands) == 3
    assert "--cron" not in commands[0]
    assert commands[1][3:6] == ["deployment", "schedule", "ls"]
    assert commands[2][3:6] == ["deployment", "schedule", "delete"]
    assert commands[2][-3:] == ["auto-notify-flow/notify-日报", "schedule-1", "--accept-yes"]
    assert result["scheduleStatus"] == "none"


def test_publish_with_multiple_enabled_schedules(monkeypatch, tmp_path):
    commands = _patch_publish(monkeypatch, tmp_path)

    result = prefect_runner.publish_config(
        {
            "name": "日报",
            "enabled": True,
            "deployment": {"crons": ["0 9 * * *", "0 17 * * *"], "timezone": "Asia/Shanghai"},
        }
    )

    assert len(commands) == 3
    assert commands[1][3:6] == ["deployment", "schedule", "create"]
    assert commands[1][commands[1].index("--cron") + 1] == "0 9 * * *"
    assert commands[1][-1] == "--replace"
    assert commands[2][3:6] == ["deployment", "schedule", "create"]
    assert commands[2][commands[2].index("--cron") + 1] == "0 17 * * *"
    assert "--replace" not in commands[2]
    assert result["scheduleStatus"] == "enabled"
    assert result["crons"] == ["0 9 * * *", "0 17 * * *"]


def test_publish_with_disabled_schedules_pauses_all(monkeypatch, tmp_path):
    commands = _patch_publish(monkeypatch, tmp_path)

    result = prefect_runner.publish_config(
        {"name": "日报", "enabled": False, "deployment": {"crons": ["0 9 * * *"], "timezone": "Asia/Shanghai"}}
    )

    assert len(commands) == 3
    assert commands[1][3:6] == ["deployment", "schedule", "create"]
    assert commands[1][commands[1].index("--cron") + 1] == "0 9 * * *"
    assert commands[2][3:6] == ["deployment", "schedule", "pause"]
    assert commands[2][-2:] == ["auto-notify-flow/notify-日报", "--all"]
    assert result["scheduleStatus"] == "disabled"


def test_publish_treats_already_paused_schedules_as_success(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(prefect_runner, "PROJECT_ROOT", tmp_path)
    task_path = tmp_path / "config" / "tasks" / "task.json"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(prefect_runner, "write_task_config", lambda *args, **kwargs: task_path)

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[3:6] == ["deployment", "schedule", "pause"]:
            return _Completed(stdout="Deployment schedules are already paused", returncode=1)
        return _Completed()

    monkeypatch.setattr(prefect_runner.subprocess, "run", fake_run)

    result = prefect_runner.publish_config(
        {"name": "日报", "enabled": False, "deployment": {"crons": ["0 9 * * *"], "timezone": "Asia/Shanghai"}}
    )

    assert len(commands) == 3
    assert result["scheduleStatus"] == "disabled"


def test_validate_config_requires_compare_engine_and_workers():
    issues = prefect_runner.validate_config(
        {
            "name": "日报",
            "template_path": "templates/missing.xlsx",
            "downloads": [
                {
                    "name": "下载",
                    "stage": "report_analysis",
                    "method": "POST",
                    "url": "https://example/export",
                    "body_type": "form",
                    "response_mode": "file",
                }
            ],
            "compare_sources": [
                {
                    "download_name": "下载",
                    "sheet_mappings": [{"new_sheet_name": "源", "template_sheet_name": "模板"}],
                }
            ],
            "send": {"items": [{"type": "image", "sheet": "通报"}]},
        }
    )

    paths = {issue["path"] for issue in issues}
    assert "/compare_sources/0/engine" in paths
    assert "/compare_sources/0/max_workers" in paths
