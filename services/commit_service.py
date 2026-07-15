"""Commit updated templates after notification succeeds."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from services.runtime_paths import is_runtime_relative_path, resolve_runtime_relative_path


PROJECT_DIR = Path(__file__).resolve().parents[1]


def resolve_project_path(value, base_dir=PROJECT_DIR):
    if value is None or value == "":
        return None
    path = Path(value)
    if is_runtime_relative_path(path):
        return resolve_runtime_relative_path(path)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def load_json(path):
    resolved = Path(path).resolve()
    with resolved.open("r", encoding="utf-8") as f:
        return json.load(f), resolved


def write_json(path, payload):
    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return resolved


def send_item_success(item):
    send = item.get("send") or {}
    return send.get("errcode") == 0


def send_item_dry_run(item):
    send = item.get("send") or {}
    return bool(send.get("dry_run"))


def validate_send_result(send_result, require_send_success=True, allow_dry_run_commit=False):
    results = send_result.get("results") or []
    if not results:
        raise RuntimeError("发送结果为空，拒绝提交模板")

    dry_run_items = [item for item in results if send_item_dry_run(item)]
    if dry_run_items and not allow_dry_run_commit:
        raise RuntimeError("发送结果是 dry-run，拒绝提交模板")

    failed_items = [item for item in results if not send_item_success(item)]
    if require_send_success and failed_items:
        raise RuntimeError(f"存在发送失败项，拒绝提交模板: {len(failed_items)}")

    return {
        "total": len(results),
        "success": sum(1 for item in results if send_item_success(item)),
        "dry_run": len(dry_run_items),
        "failed": len(failed_items),
    }


def validate_update_manifest(update_manifest):
    status = update_manifest.get("status")
    if status == "skipped":
        return {
            "status": "skipped",
            "reason": update_manifest.get("reason") or "update_skipped",
        }
    if status != "updated":
        raise RuntimeError(f"模板更新清单状态不是 updated: {status}")

    output_path = Path(update_manifest.get("output_path") or "")
    template_path = Path(update_manifest.get("template_path") or "")
    if not output_path.exists():
        raise FileNotFoundError(f"临时模板不存在: {output_path}")
    if not template_path.exists():
        raise FileNotFoundError(f"正式模板不存在: {template_path}")
    return {
        "status": "updated",
        "output_path": output_path,
        "template_path": template_path,
    }


def backup_template(template_path, backup_dir):
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{template_path.stem}_backup_{timestamp}{template_path.suffix}"
    shutil.copy2(template_path, backup_path)
    return backup_path


def commit_template(config, base_dir=PROJECT_DIR):
    update_manifest_path = resolve_project_path(config.get("update_manifest_path"), base_dir)
    send_result_path = resolve_project_path(config.get("send_result_path"), base_dir)
    backup_dir = resolve_project_path(config.get("backup_dir", "modules/template_commit/output/backup"), base_dir)
    manifest_path = resolve_project_path(config.get("manifest_path", "modules/template_commit/output/commit_manifest.json"), base_dir)

    if not update_manifest_path:
        raise ValueError("缺少 update_manifest_path")
    if not send_result_path:
        raise ValueError("缺少 send_result_path")

    update_manifest, _ = load_json(update_manifest_path)
    send_result, _ = load_json(send_result_path)

    try:
        update_state = validate_update_manifest(update_manifest)
    except Exception as exc:
        manifest = {
            "status": "blocked",
            "reason": str(exc),
            "update_manifest_path": str(update_manifest_path),
            "send_result_path": str(send_result_path),
            "generated_at": datetime.now().isoformat(),
        }
        write_json(manifest_path, manifest)
        raise

    if update_state["status"] == "skipped":
        manifest = {
            "status": "skipped",
            "reason": update_state["reason"],
            "update_manifest_path": str(update_manifest_path),
            "send_result_path": str(send_result_path),
            "generated_at": datetime.now().isoformat(),
        }
        write_json(manifest_path, manifest)
        return manifest

    try:
        send_summary = validate_send_result(
            send_result,
            require_send_success=bool(config.get("require_send_success", True)),
            allow_dry_run_commit=bool(config.get("allow_dry_run_commit", False)),
        )
    except Exception as exc:
        manifest = {
            "status": "blocked",
            "reason": str(exc),
            "template_path": str(update_state["template_path"]),
            "updated_template_path": str(update_state["output_path"]),
            "update_manifest_path": str(update_manifest_path),
            "send_result_path": str(send_result_path),
            "generated_at": datetime.now().isoformat(),
        }
        write_json(manifest_path, manifest)
        raise

    template_path = update_state["template_path"]
    output_path = update_state["output_path"]
    backup_path = None
    committed = False
    if bool(config.get("overwrite", True)):
        backup_path = backup_template(template_path, backup_dir)
        shutil.copy2(output_path, template_path)
        committed = True

    manifest = {
        "status": "committed" if committed else "validated",
        "template_path": str(template_path),
        "updated_template_path": str(output_path),
        "backup_path": str(backup_path) if backup_path else None,
        "send_summary": send_summary,
        "updated_sheets": update_manifest.get("updated_sheets", []),
        "generated_at": datetime.now().isoformat(),
    }
    write_json(manifest_path, manifest)
    return manifest


def commit_template_from_config(config_path, base_dir=PROJECT_DIR):
    config_path = resolve_project_path(config_path, base_dir)
    config, resolved = load_json(config_path)
    return commit_template(config, base_dir=resolved.parent)
