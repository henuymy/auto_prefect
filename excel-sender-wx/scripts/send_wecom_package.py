"""Send a prepared message package to a WeCom group robot webhook."""

import argparse
import base64
import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib import request


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG_PATH = SKILL_DIR / "config.json"


def load_json(path):
    resolved = Path(path).resolve()
    with resolved.open("r", encoding="utf-8") as f:
        return json.load(f), resolved


def resolve_path(value, base_dir):
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path


def get_package_file(config, config_dir):
    output_config = config.get("output", {})
    runtime_dir = resolve_path(output_config.get("runtime_dir", "runtime"), config_dir)
    return resolve_path(output_config.get("package_file", "message_package.json"), runtime_dir)


def get_preview_file(config, config_dir):
    output_config = config.get("output", {})
    runtime_dir = resolve_path(output_config.get("runtime_dir", "runtime"), config_dir)
    return resolve_path(output_config.get("preview_image_file", "preview.png"), runtime_dir)


def get_image_dir(config, config_dir):
    output_config = config.get("output", {})
    runtime_dir = resolve_path(output_config.get("runtime_dir", "runtime"), config_dir)
    return resolve_path(output_config.get("image_dir", "images"), runtime_dir)


def get_result_file(config, config_dir):
    output_config = config.get("output", {})
    runtime_dir = resolve_path(output_config.get("runtime_dir", "runtime"), config_dir)
    return resolve_path(output_config.get("send_result_file", "send_result.json"), runtime_dir)


def is_placeholder_webhook(url):
    return not url or "YOUR_KEY" in url


def post_json(url, payload, timeout=30):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def send_text(webhook_url, text, timeout=30):
    payload = {
        "msgtype": "text",
        "text": {
            "content": text,
        },
    }
    return post_json(webhook_url, payload, timeout=timeout)


def image_payload_from_file(image_path):
    image_bytes = Path(image_path).read_bytes()
    return {
        "base64": base64.b64encode(image_bytes).decode("ascii"),
        "md5": hashlib.md5(image_bytes).hexdigest(),
    }


def send_image_payload(webhook_url, image_payload, timeout=30):
    payload = {
        "msgtype": "image",
        "image": {
            "base64": image_payload["base64"],
            "md5": image_payload["md5"],
        },
    }
    return post_json(webhook_url, payload, timeout=timeout)


def send_image(webhook_url, image_path, timeout=30):
    return send_image_payload(webhook_url, image_payload_from_file(image_path), timeout=timeout)


def save_results(result_file, results):
    result_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "results": results,
    }
    with result_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def successful_send(results):
    return all(item.get("send", {}).get("errcode") == 0 for item in results)


def remove_file(path):
    if path.exists() and path.is_file():
        path.unlink()
        print(f"[INFO] 已删除临时文件: {path}")


def cleanup_outputs(config, config_dir, package_file=None):
    output_config = config.get("output", {})
    if not output_config.get("cleanup_after_send", True):
        return

    package_path = Path(package_file).resolve() if package_file else get_package_file(config, config_dir)
    preview_path = get_preview_file(config, config_dir)
    image_dir = get_image_dir(config, config_dir)

    remove_file(package_path)
    remove_file(preview_path)

    if image_dir.exists() and image_dir.is_dir():
        for child in image_dir.iterdir():
            if child.is_file():
                child.unlink()
                print(f"[INFO] 已删除临时文件: {child}")
        try:
            image_dir.rmdir()
            print(f"[INFO] 已删除临时目录: {image_dir}")
        except OSError:
            pass


def send_package(package, webhook_url, dry_run=False, timeout=30):
    if not dry_run and is_placeholder_webhook(webhook_url):
        raise ValueError("企业微信 webhook_url 还是占位值，请先在 config.json 中填写真实地址，或使用 --dry-run")

    results = []
    for item in package.get("items", []):
        item_type = item.get("type")
        result = {
            "workbook": item.get("workbook"),
            "report": item.get("report"),
            "item_index": item.get("item_index"),
            "type": item_type,
            "sheet": item.get("sheet"),
        }

        if dry_run:
            result["send"] = {"dry_run": True, "sent": False}
        elif item_type == "image":
            result["send"] = send_image_payload(webhook_url, item["image"], timeout=timeout)
        elif item_type == "text":
            result["send"] = send_text(webhook_url, item["text"], timeout=timeout)
        else:
            raise ValueError(f"消息包中存在不支持的 item.type: {item_type}")

        results.append(result)
        print(f"[INFO] 发送处理完成: {result['type']} / {result['workbook']} / {result['report']} / {result['sheet']}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Send a prepared message package to WeCom.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--package")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    config, config_path = load_json(args.config)
    config_dir = config_path.parent
    package_file = Path(args.package).resolve() if args.package else get_package_file(config, config_dir)
    package, _ = load_json(package_file)
    webhook_url = config.get("wecom", {}).get("webhook_url", "")

    results = send_package(package, webhook_url, dry_run=args.dry_run, timeout=args.timeout)
    result_file = get_result_file(config, config_dir)
    save_results(result_file, results)
    if not args.dry_run and successful_send(results):
        cleanup_outputs(config, config_dir, package_file=package_file)
    print(f"[INFO] 发送结果已写入: {result_file}")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
