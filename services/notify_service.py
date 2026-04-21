"""Send prepared notification packages."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from infrastructure.wecom_client import send_text, send_image_payload


PROJECT_DIR = Path(__file__).resolve().parents[1]


def resolve_path(value, base_dir=PROJECT_DIR):
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path


def get_output_path(config, name, default, base_dir=PROJECT_DIR):
    output_config = config.get("output", {})
    runtime_dir = resolve_path(output_config.get("runtime_dir", "runtime"), base_dir)
    return resolve_path(output_config.get(name, default), runtime_dir)


def get_result_file(config, base_dir=PROJECT_DIR):
    return get_output_path(config, "send_result_file", "send_result.json", base_dir)


def get_package_file(config, base_dir=PROJECT_DIR):
    return get_output_path(config, "package_file", "message_package.json", base_dir)


def get_preview_file(config, base_dir=PROJECT_DIR):
    return get_output_path(config, "preview_image_file", "preview.png", base_dir)


def get_image_dir(config, base_dir=PROJECT_DIR):
    return get_output_path(config, "image_dir", "images", base_dir)


def is_placeholder_webhook(url):
    return not url or "YOUR_KEY" in url


def save_results(result_file, results):
    result_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": datetime.now().isoformat(), "results": results}
    with result_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def successful_send(results):
    return all(item.get("send", {}).get("errcode") == 0 for item in results)


def cleanup_outputs(config, base_dir=PROJECT_DIR, package_file=None):
    output_config = config.get("output", {})
    if not output_config.get("cleanup_after_send", True):
        return
    package_path = Path(package_file).resolve() if package_file else get_package_file(config, base_dir)
    preview_path = get_preview_file(config, base_dir)
    image_dir = get_image_dir(config, base_dir)
    for path in (package_path, preview_path):
        if path.exists() and path.is_file():
            path.unlink()
    if image_dir.exists() and image_dir.is_dir():
        for child in image_dir.iterdir():
            if child.is_file():
                child.unlink()
        try:
            image_dir.rmdir()
        except OSError:
            pass


def send_package(package, webhook_url, dry_run=False, timeout=30):
    if not dry_run and is_placeholder_webhook(webhook_url):
        raise ValueError("企业微信 webhook_url 还是占位值，请先填写真实地址，或使用 dry_run")
    results = []
    for item in package.get("items", []):
        item_type = item.get("type")
        result = {"workbook": item.get("workbook"), "report": item.get("report"), "item_index": item.get("item_index"), "type": item_type, "sheet": item.get("sheet")}
        if dry_run:
            result["send"] = {"dry_run": True, "sent": False}
        elif item_type == "image":
            result["send"] = send_image_payload(webhook_url, item["image"], timeout=timeout)
        elif item_type == "text":
            result["send"] = send_text(webhook_url, item["text"], timeout=timeout)
        else:
            raise ValueError(f"消息包中存在不支持的 item.type: {item_type}")
        results.append(result)
    return results


def send_notification_package(config, package, package_file=None, dry_run=False, timeout=30, base_dir=PROJECT_DIR):
    webhook_url = config.get("wecom", {}).get("webhook_url", "")
    results = send_package(package, webhook_url, dry_run=dry_run, timeout=timeout)
    result_file = get_result_file(config, base_dir)
    payload = save_results(result_file, results)
    if not dry_run and successful_send(results):
        cleanup_outputs(config, base_dir, package_file=package_file)
    return {"result_file": str(result_file), "results": results, "generated_at": payload["generated_at"]}
