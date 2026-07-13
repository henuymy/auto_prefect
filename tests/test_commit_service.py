import json
import shutil
import tempfile
from pathlib import Path

import pytest

from services.commit_service import commit_template, resolve_project_path


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_work_dir():
    return Path(tempfile.mkdtemp(prefix="auto_notify_commit_test_"))


def test_resolve_project_path_rebases_logical_runtime_paths(monkeypatch, tmp_path):
    runtime_root = tmp_path / "shared-runtime"
    monkeypatch.setenv("AUTO_NOTIFY_RUNTIME_ROOT", str(runtime_root))

    assert resolve_project_path("runtime/flow/日报/debug/update_manifest.json") == (
        runtime_root / "flow" / "日报" / "debug" / "update_manifest.json"
    ).resolve()


def test_commit_template_after_successful_send():
    work_dir = make_work_dir()
    template_path = work_dir / "official.xlsx"
    updated_path = work_dir / "updated.xlsx"
    update_manifest_path = work_dir / "update_manifest.json"
    send_result_path = work_dir / "send_result.json"
    manifest_path = work_dir / "commit_manifest.json"

    try:
        template_path.write_text("old", encoding="utf-8")
        updated_path.write_text("new", encoding="utf-8")
        write_json(
            update_manifest_path,
            {
                "status": "updated",
                "template_path": str(template_path),
                "output_path": str(updated_path),
                "updated_sheets": ["通报"],
            },
        )
        write_json(send_result_path, {"results": [{"send": {"errcode": 0}}]})

        manifest = commit_template(
            {
                "update_manifest_path": str(update_manifest_path),
                "send_result_path": str(send_result_path),
                "backup_dir": str(work_dir / "backups"),
                "manifest_path": str(manifest_path),
                "overwrite": True,
            }
        )

        assert manifest["status"] == "committed"
        assert manifest["send_summary"]["success"] == 1
        assert template_path.read_text(encoding="utf-8") == "new"
        assert manifest_path.exists()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def test_commit_template_blocks_failed_send():
    work_dir = make_work_dir()
    template_path = work_dir / "official.xlsx"
    updated_path = work_dir / "updated.xlsx"
    update_manifest_path = work_dir / "update_manifest.json"
    send_result_path = work_dir / "send_result.json"
    manifest_path = work_dir / "commit_manifest.json"

    try:
        template_path.write_text("old", encoding="utf-8")
        updated_path.write_text("new", encoding="utf-8")
        write_json(
            update_manifest_path,
            {
                "status": "updated",
                "template_path": str(template_path),
                "output_path": str(updated_path),
            },
        )
        write_json(send_result_path, {"results": [{"send": {"errcode": 1}}]})

        with pytest.raises(RuntimeError, match="存在发送失败项"):
            commit_template(
                {
                    "update_manifest_path": str(update_manifest_path),
                    "send_result_path": str(send_result_path),
                    "backup_dir": str(work_dir / "backups"),
                    "manifest_path": str(manifest_path),
                    "overwrite": True,
                }
            )

        assert template_path.read_text(encoding="utf-8") == "old"
        blocked_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert blocked_manifest["status"] == "blocked"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
