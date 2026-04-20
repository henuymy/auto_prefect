"""Gotify OTP provider for SMSForwarder-based login."""

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib import error, parse, request


@dataclass
class OtpResult:
    code: str
    message_id: Optional[int]
    title: str


@dataclass
class OtpWaitContext:
    trigger_time: datetime
    latest_message_id: Optional[int]


def mask_code(code):
    if len(code) <= 2:
        return "*" * len(code)
    return f"{code[:2]}{'*' * (len(code) - 2)}"


def _parse_gotify_date(value):
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _message_text(message):
    title = message.get("title") or ""
    body = message.get("message") or ""
    return f"{title}\n{body}"


def _matches_title(message, title_prefix):
    if not title_prefix:
        return True
    return (message.get("title") or "").startswith(title_prefix)


def _matches_keywords(text, keywords):
    if not keywords:
        return True
    return any(keyword in text for keyword in keywords)


def _matches_sender(text, allowed_senders):
    if not allowed_senders:
        return True
    return any(sender in text for sender in allowed_senders)


def _matches_time(message, trigger_time, ttl_seconds, grace_seconds=30):
    message_time = _parse_gotify_date(message.get("date"))
    if not message_time:
        return True
    if message_time < trigger_time - timedelta(seconds=grace_seconds):
        return False
    return (message_time - trigger_time).total_seconds() <= ttl_seconds


def _request_json(url, client_token, method="GET", timeout=10):
    req = request.Request(url, method=method)
    req.add_header("X-Gotify-Key", client_token)
    opener = request.build_opener(request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        if method == "DELETE":
            return {}
        data = resp.read().decode("utf-8")
        return json.loads(data)


def fetch_messages(otp_config):
    gotify_url = otp_config["gotify_url"].rstrip("/")
    client_token = otp_config["client_token"]
    limit = int(otp_config.get("fetch_limit", 20))
    timeout = int(otp_config.get("request_timeout_seconds", 10))
    query = parse.urlencode({"limit": limit})
    url = f"{gotify_url}/message?{query}"
    payload = _request_json(url, client_token, timeout=timeout)
    return payload.get("messages", [])


def delete_message(otp_config, message_id):
    if not message_id:
        return False
    gotify_url = otp_config["gotify_url"].rstrip("/")
    client_token = otp_config["client_token"]
    timeout = int(otp_config.get("request_timeout_seconds", 10))
    url = f"{gotify_url}/message/{message_id}"
    _request_json(url, client_token, method="DELETE", timeout=timeout)
    return True


def prepare_wait_context(otp_config):
    """Record the latest Gotify message before requesting a new OTP."""
    try:
        messages = fetch_messages(otp_config)
        latest_message_id = max(
            (
                message["id"]
                for message in messages
                if isinstance(message.get("id"), int)
            ),
            default=None,
        )
        print(f"[INFO] Gotify 验证码等待基线已建立，最新消息ID: {latest_message_id}")
    except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
        latest_message_id = None
        print(f"[WARN] 建立 Gotify 消息基线失败，将仅使用时间窗口匹配: {exc}")

    return OtpWaitContext(
        trigger_time=datetime.now(timezone.utc),
        latest_message_id=latest_message_id,
    )


def extract_otp(text, code_regex, keywords=None, preferred_lengths=None):
    matches = list(re.finditer(code_regex, text))
    if not matches:
        return None

    keywords = keywords or []
    keyword_positions = [
        found.start()
        for keyword in keywords
        for found in re.finditer(re.escape(keyword), text)
    ]
    if keyword_positions:
        match = min(
            matches,
            key=lambda item: min(abs(item.start() - pos) for pos in keyword_positions),
        )
        return match.group(1) if match.groups() else match.group(0)

    preferred_lengths = [int(length) for length in (preferred_lengths or [])]
    if preferred_lengths:
        for length in preferred_lengths:
            for match in matches:
                value = match.group(1) if match.groups() else match.group(0)
                if len(value) == length:
                    return value

    match = matches[0]
    return match.group(1) if match.groups() else match.group(0)


def match_and_extract_code(messages, otp_config, trigger_time, latest_message_id=None):
    title_prefix = otp_config.get("title_prefix", "")
    allowed_senders = otp_config.get("allowed_senders", [])
    required_keywords = otp_config.get("required_keywords", [])
    code_regex = otp_config.get("code_regex", r"(?<!\d)(\d{4,8})(?!\d)")
    preferred_code_lengths = otp_config.get("preferred_code_lengths", [])
    ttl_seconds = int(otp_config.get("message_ttl_seconds", 600))
    trigger_grace_seconds = int(otp_config.get("trigger_grace_seconds", 30))
    require_new_message = otp_config.get("require_new_message", True)

    for message in messages:
        message_id = message.get("id")
        if (
            require_new_message
            and latest_message_id is not None
            and isinstance(message_id, int)
            and message_id <= latest_message_id
        ):
            continue

        text = _message_text(message)
        if not _matches_title(message, title_prefix):
            continue
        if not _matches_time(message, trigger_time, ttl_seconds, trigger_grace_seconds):
            continue
        if not _matches_sender(text, allowed_senders):
            continue
        if not _matches_keywords(text, required_keywords):
            continue

        code = extract_otp(text, code_regex, required_keywords, preferred_code_lengths)
        if code:
            return OtpResult(
                code=code,
                message_id=message.get("id"),
                title=message.get("title") or "",
            )

    return None


def summarize_messages(messages, otp_config, latest_message_id=None, limit=5):
    title_prefix = otp_config.get("title_prefix", "")
    allowed_senders = otp_config.get("allowed_senders", [])
    required_keywords = otp_config.get("required_keywords", [])
    code_regex = otp_config.get("code_regex", r"(?<!\d)(\d{4,8})(?!\d)")
    regex = re.compile(code_regex)
    summary = []

    for message in messages[:limit]:
        text = _message_text(message)
        digits = regex.findall(text)
        summary.append(
            {
                "id": message.get("id"),
                "title": message.get("title"),
                "date": message.get("date"),
                "is_baseline_or_older": (
                    latest_message_id is not None
                    and isinstance(message.get("id"), int)
                    and message.get("id") <= latest_message_id
                ),
                "title_match": _matches_title(message, title_prefix),
                "sender_match": _matches_sender(text, allowed_senders),
                "keyword_hits": [kw for kw in required_keywords if kw in text],
                "digit_lengths": [
                    len(item if isinstance(item, str) else item[0])
                    for item in digits[:5]
                ],
            }
        )

    return summary


def wait_for_otp(
    otp_config: dict[str, Any],
    trigger_time: datetime,
    latest_message_id=None,
) -> OtpResult:
    """Poll Gotify until a matching OTP message appears."""
    timeout_seconds = int(otp_config.get("timeout_seconds", 180))
    poll_interval = int(otp_config.get("poll_interval_seconds", 3))
    started_at = time.monotonic()
    last_error = None
    last_messages = []

    while time.monotonic() - started_at < timeout_seconds:
        try:
            messages = fetch_messages(otp_config)
            last_messages = messages
            result = match_and_extract_code(
                messages,
                otp_config,
                trigger_time,
                latest_message_id=latest_message_id,
            )
            if result:
                print(f"[INFO] Gotify 已匹配验证码消息: title={result.title!r}, code={mask_code(result.code)}")
                return result
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            last_error = exc
            print(f"[WARN] Gotify 拉取验证码失败，稍后重试: {exc}")

        time.sleep(poll_interval)

    if last_error:
        raise TimeoutError(f"等待 Gotify 验证码超时，最后错误: {last_error}")
    if last_messages:
        print("[WARN] Gotify 最近消息脱敏诊断:")
        for item in summarize_messages(last_messages, otp_config, latest_message_id):
            print(f"[WARN] {item}")
    raise TimeoutError("等待 Gotify 验证码超时")
