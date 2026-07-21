from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from utils import config_loader


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_managed_module_uses_runtime_override_and_ignores_sidecar(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    module_path = project_root / "config" / "modules" / "tencent_docs.json"
    write_json(
        module_path,
        {
            "credentials": {"client_id": ""},
            "retry": {"attempts": 3},
        },
    )
    write_json(
        module_path.with_name("tencent_docs.local.json"),
        {"credentials": {"client_id": "legacy"}},
    )
    write_json(
        project_root / "config" / "runtime.local.json",
        {
            "module_overrides": {
                "tencent_docs": {"credentials": {"client_id": "central"}}
            }
        },
    )

    payload, resolved = config_loader.load_json_with_runtime_override(
        module_path,
        project_root=project_root,
    )

    assert resolved == module_path.resolve()
    assert payload == {
        "credentials": {"client_id": "central"},
        "retry": {"attempts": 3},
    }


def test_dashboard_session_uses_runtime_session_overrides(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    session_path = project_root / "config" / "dashboard" / "session.json"
    write_json(
        session_path,
        {
            "collection_database_lock_name": "auto_notify_dashboard_collection",
            "partition_database_lock_name": "auto_notify_dashboard_partition_maintenance",
        },
    )
    write_json(
        session_path.with_name("session.local.json"),
        {"collection_database_lock_name": "legacy-lock"},
    )
    write_json(
        project_root / "config" / "runtime.local.json",
        {
            "dashboard": {
                "session_overrides": {
                    "collection_database_lock_name": "auto_notify_dashboard_collection_dev"
                }
            }
        },
    )

    payload, _ = config_loader.load_json_with_runtime_override(
        session_path,
        project_root=project_root,
    )

    assert payload["collection_database_lock_name"] == "auto_notify_dashboard_collection_dev"
    assert (
        payload["partition_database_lock_name"]
        == "auto_notify_dashboard_partition_maintenance"
    )


def test_managed_task_uses_runtime_task_override(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    task_path = project_root / "config" / "tasks" / "日报通报.json"
    write_json(task_path, {"flow_name": "auto-notify-flow", "retry": {"attempts": 1}})
    write_json(
        project_root / "config" / "runtime.local.json",
        {"task_overrides": {"日报通报": {"retry": {"attempts": 2}}}},
    )

    payload, _ = config_loader.load_json_with_runtime_override(
        task_path,
        project_root=project_root,
    )

    assert payload == {
        "flow_name": "auto-notify-flow",
        "retry": {"attempts": 2},
    }


def test_unknown_module_override_target_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    runtime_path = project_root / "config" / "runtime.local.json"
    write_json(runtime_path, {"module_overrides": {"missing": {}}})

    with pytest.raises(ValueError, match=r"module_overrides\.missing"):
        config_loader.validate_runtime_local_overrides(
            json.loads(runtime_path.read_text(encoding="utf-8")),
            project_root=project_root,
        )


def test_non_object_task_override_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    task_path = project_root / "config" / "tasks" / "日报通报.json"
    write_json(task_path, {"flow_name": "auto-notify-flow"})
    runtime_path = project_root / "config" / "runtime.local.json"
    write_json(runtime_path, {"task_overrides": {"日报通报": "invalid"}})

    with pytest.raises(ValueError, match=r"task_overrides\.日报通报"):
        config_loader.load_json_with_runtime_override(
            task_path,
            project_root=project_root,
        )


def test_invalid_override_key_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "project"

    with pytest.raises(ValueError, match=r"module_overrides\.bad/name"):
        config_loader.validate_runtime_local_overrides(
            {"module_overrides": {"bad/name": {}}},
            project_root=project_root,
        )


def test_unmanaged_json_does_not_read_runtime_overrides(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    config_path = project_root / "temporary.json"
    write_json(config_path, {"source": "temporary"})
    write_json(
        project_root / "config" / "runtime.local.json",
        {"module_overrides": {"missing": {}}},
    )

    payload, _ = config_loader.load_json_with_runtime_override(
        config_path,
        project_root=project_root,
    )

    assert payload == {"source": "temporary"}


def test_runtime_template_declares_ordered_override_sections() -> None:
    root = Path(__file__).resolve().parents[1]
    template = json.loads(
        (root / "config" / "runtime.local.example.json").read_text(encoding="utf-8")
    )

    assert list(template) == [
        "runtime",
        "prefect",
        "dashboard",
        "monitor",
        "module_overrides",
        "task_overrides",
    ]
    assert template["dashboard"]["session_overrides"]
    assert set(template["module_overrides"]) == {
        "login_config",
        "tencent_docs",
        "wecom_sender",
    }
    assert template["task_overrides"] == {}


def import_runtime_config(config_path: Path) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell is not None
    root = Path(__file__).resolve().parents[1]
    script_path = (root / "scripts" / "lib" / "runtime_config.ps1").as_posix()
    command = f"""
$ErrorActionPreference = 'Stop'
. '{script_path}'
Import-RuntimeConfig -ConfigPath '{config_path.as_posix()}' | Out-Null
"""
    return subprocess.run(
        [powershell, "-NoProfile", "-Command", command],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_runtime_import_rejects_non_object_override_section(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (root / "config" / "runtime.local.example.json").read_text(encoding="utf-8")
    )
    payload["module_overrides"] = []
    config_path = tmp_path / "runtime.invalid-overrides.json"
    write_json(config_path, payload)

    result = import_runtime_config(config_path)

    assert result.returncode != 0
    assert "运行配置必须为对象: module_overrides" in result.stdout + result.stderr
