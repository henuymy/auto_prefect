"""WeCom robot client utilities."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib import request


def post_json(url, payload, timeout=30):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_text(webhook_url, text, timeout=30):
    return post_json(webhook_url, {"msgtype": "text", "text": {"content": text}}, timeout=timeout)


def send_image_payload(webhook_url, image_payload, timeout=30):
    return post_json(webhook_url, {"msgtype": "image", "image": {"base64": image_payload["base64"], "md5": image_payload["md5"]}}, timeout=timeout)


def image_payload_from_file(image_path):
    image_bytes = Path(image_path).read_bytes()
    return {"base64": base64.b64encode(image_bytes).decode("ascii"), "md5": hashlib.md5(image_bytes).hexdigest()}
