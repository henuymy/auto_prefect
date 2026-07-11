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

    assert len(commands) == 5
    assert commands[1][3:6] == ["deployment", "schedule", "create"]
    assert commands[1][commands[1].index("--cron") + 1] == "0 9 * * *"
    assert commands[1][-1] == "--replace"
    assert commands[2][3:6] == ["deployment", "schedule", "create"]
    assert commands[2][commands[2].index("--cron") + 1] == "0 17 * * *"
    assert "--replace" not in commands[2]
    assert result["scheduleStatus"] == "enabled"
    assert commands[3][3:6] == ["deployment", "schedule", "ls"]
    assert commands[4][3:6] == ["deployment", "schedule", "resume"]
    assert commands[4][-2:] == ["auto-notify-flow/notify-日报", "schedule-1"]
    assert result["crons"] == ["0 9 * * *", "0 17 * * *"]


def test_publish_with_disabled_schedules_pauses_all(monkeypatch, tmp_path):
    commands = _patch_publish(monkeypatch, tmp_path)

    result = prefect_runner.publish_config(
        {"name": "日报", "enabled": False, "deployment": {"crons": ["0 9 * * *"], "timezone": "Asia/Shanghai"}}
    )

    assert len(commands) == 4
    assert commands[1][3:6] == ["deployment", "schedule", "create"]
    assert commands[1][commands[1].index("--cron") + 1] == "0 9 * * *"
    assert commands[2][3:6] == ["deployment", "schedule", "ls"]
    assert commands[3][3:6] == ["deployment", "schedule", "pause"]
    assert commands[3][-2:] == ["auto-notify-flow/notify-日报", "schedule-1"]
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
        if command[3:6] == ["deployment", "schedule", "ls"]:
            return _Completed(stdout='[{"id":"schedule-1"}]')
        if command[3:6] == ["deployment", "schedule", "pause"]:
            return _Completed(stdout="Deployment schedule schedule-1 is already paused", returncode=1)
        return _Completed()

    monkeypatch.setattr(prefect_runner.subprocess, "run", fake_run)

    result = prefect_runner.publish_config(
        {"name": "日报", "enabled": False, "deployment": {"crons": ["0 9 * * *"], "timezone": "Asia/Shanghai"}}
    )

    assert len(commands) == 4
    assert result["scheduleStatus"] == "disabled"


def test_publish_pauses_every_schedule_for_multiple_crons(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(prefect_runner, "PROJECT_ROOT", tmp_path)
    task_path = tmp_path / "config" / "tasks" / "task.json"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(prefect_runner, "write_task_config", lambda *args, **kwargs: task_path)

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[3:6] == ["deployment", "schedule", "ls"]:
            return _Completed(stdout='[{"id":"schedule-1"},{"id":"schedule-2"}]')
        return _Completed()

    monkeypatch.setattr(prefect_runner.subprocess, "run", fake_run)

    result = prefect_runner.publish_config(
        {
            "name": "日报",
            "enabled": False,
            "deployment": {"crons": ["0 9 * * *", "0 18 * * *"], "timezone": "Asia/Shanghai"},
        }
    )

    pause_commands = [command for command in commands if command[3:6] == ["deployment", "schedule", "pause"]]
    assert [command[-1] for command in pause_commands] == ["schedule-1", "schedule-2"]
    assert result["scheduleStatus"] == "disabled"


def test_can_fast_toggle_schedule_ignores_only_state_and_metadata():
    previous = {
        "name": "日报",
        "enabled": True,
        "deployment": {"crons": ["0 9 * * *"], "timezone": "Asia/Shanghai"},
        "updatedAt": "old",
        "source": "published",
    }
    current = {
        **previous,
        "enabled": False,
        "updatedAt": "new",
        "has_draft": False,
    }

    assert prefect_runner.can_fast_toggle_schedule(previous, current)
    current["deployment"] = {"crons": ["0 10 * * *"], "timezone": "Asia/Shanghai"}
    assert not prefect_runner.can_fast_toggle_schedule(previous, current)


def test_fast_toggle_schedule_patches_existing_matching_schedules(monkeypatch):
    calls = []
    schedules = [
        {
            "id": "schedule-1",
            "schedule": {"cron": "0 9 * * *", "timezone": "Asia/Shanghai"},
            "active": True,
        },
        {
            "id": "schedule-2",
            "schedule": {"cron": "0 18 * * *", "timezone": "Asia/Shanghai"},
            "active": True,
        },
    ]

    def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        if "/name/" in path:
            return {"id": "deployment-1"}
        if method == "GET":
            return schedules
        return None

    monkeypatch.setattr(prefect_runner, "_prefect_api_request", fake_request)

    result = prefect_runner.fast_toggle_schedule(
        {
            "name": "日报",
            "enabled": False,
            "deployment": {"crons": ["0 9 * * *", "0 18 * * *"], "timezone": "Asia/Shanghai"},
        }
    )

    patch_calls = [call for call in calls if call[0] == "PATCH"]
    assert [call[1].rsplit("/", 1)[-1] for call in patch_calls] == ["schedule-1", "schedule-2"]
    assert all(call[2] == {"active": False} for call in patch_calls)
    assert result["scheduleStatus"] == "disabled"
    assert result["publishMode"] == "schedule-state-only"


def test_fast_toggle_schedule_falls_back_when_crons_do_not_match(monkeypatch):
    def fake_request(method, path, payload=None):
        if "/name/" in path:
            return {"id": "deployment-1"}
        return [
            {
                "id": "schedule-1",
                "schedule": {"cron": "0 10 * * *", "timezone": "Asia/Shanghai"},
                "active": True,
            }
        ]

    monkeypatch.setattr(prefect_runner, "_prefect_api_request", fake_request)

    result = prefect_runner.fast_toggle_schedule(
        {
            "name": "日报",
            "enabled": False,
            "deployment": {"crons": ["0 9 * * *"], "timezone": "Asia/Shanghai"},
        }
    )

    assert result is None


def test_delete_deployment_resolves_name_then_deletes_by_id(monkeypatch):
    calls = []

    def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET":
            return {"id": "deployment-1"}
        return None

    monkeypatch.setattr(prefect_runner, "_prefect_api_request", fake_request)

    result = prefect_runner.delete_deployment({"name": "每日日报"})

    assert calls == [
        ("GET", "deployments/name/auto-notify-flow/notify-%E6%AF%8F%E6%97%A5%E6%97%A5%E6%8A%A5", None),
        ("DELETE", "deployments/deployment-1", None),
    ]
    assert result["deleted"] is True
    assert result["deploymentId"] == "deployment-1"


def test_delete_deployment_treats_missing_deployment_as_idempotent(monkeypatch):
    def fake_request(method, path, payload=None):
        raise prefect_runner.urllib.error.HTTPError(path, 404, "not found", {}, None)

    monkeypatch.setattr(prefect_runner, "_prefect_api_request", fake_request)

    result = prefect_runner.delete_deployment({"name": "日报"})

    assert result["deleted"] is False
    assert result["deploymentId"] == ""


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


def test_validate_config_requires_compare_sources_when_enabled():
    issues = prefect_runner.validate_config(
        {
            "name": "日报",
            "template_path": "templates/missing.xlsx",
            "enabled": True,
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
            "compare_sources": [],
            "send": {"items": [{"type": "image", "sheet": "通报"}]},
        }
    )

    assert "/compare_sources" in {issue["path"] for issue in issues}


def test_validate_config_requires_explicit_send_ranges():
    config = {
        "name": "日报",
        "template_path": "templates/missing.xlsx",
        "downloads": [],
        "compare_sources": [],
        "send": {
            "items": [
                {"type": "image", "sheet": "图片", "capture": {"mode": "explicit_range", "range": ""}},
                {"type": "text", "sheet": "文字", "text": {"mode": "explicit_range"}},
            ]
        },
    }

    paths = {issue["path"] for issue in prefect_runner.validate_config(config)}

    assert "/send/items/0/capture/range" in paths
    assert "/send/items/1/text/range" in paths
