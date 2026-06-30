"""Gotify client utilities."""

from __future__ import annotations

import json
from urllib import parse, request


def _request_json(url, client_token, method="GET", timeout=10):
    req = request.Request(url, method=method)
    req.add_header("X-Gotify-Key", client_token)
    opener = request.build_opener(request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        if method == "DELETE":
            return {}
        return json.loads(resp.read().decode("utf-8"))


def fetch_messages(otp_config):
    gotify_url = otp_config["gotify_url"].rstrip("/")
    limit = int(otp_config.get("fetch_limit", 20))
    timeout = int(otp_config.get("request_timeout_seconds", 10))
    url = f"{gotify_url}/message?{parse.urlencode({'limit': limit})}"
    return _request_json(url, otp_config["client_token"], timeout=timeout).get("messages", [])


def delete_message(otp_config, message_id):
    if not message_id:
        return False
    gotify_url = otp_config["gotify_url"].rstrip("/")
    timeout = int(otp_config.get("request_timeout_seconds", 10))
    _request_json(f"{gotify_url}/message/{message_id}", otp_config["client_token"], method="DELETE", timeout=timeout)
    return True
