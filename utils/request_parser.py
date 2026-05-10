"""Helpers for turning copied browser requests into report download config."""

from __future__ import annotations

import json
import re
import shlex
from urllib.parse import parse_qsl, urlparse


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
SKIP_HEADER_NAMES = {
    "cookie",
    "content-length",
    "host",
    "connection",
    "accept-encoding",
}
SKIP_HEADER_PREFIXES = ("sec-", "proxy-")


def _append_payload_value(payload: dict, key: str, value: str):
    if key in payload:
        if isinstance(payload[key], list):
            payload[key].append(value)
        else:
            payload[key] = [payload[key], value]
    else:
        payload[key] = value


def parse_headers_text(raw_text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    current_key = ""
    for raw_line in (raw_text or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line[0].isspace() and current_key:
            headers[current_key] = f"{headers[current_key]} {line.strip()}"
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        headers[key] = value.strip()
        current_key = key
    return headers


def _strip_wrapping_quotes(value: str) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def normalize_copied_command(raw_text: str) -> str:
    text = (raw_text or "").replace("\r\n", "\n").strip()
    # Chrome's "Copy as cURL (cmd)" uses ^ for line continuation.
    text = re.sub(r"\s*\^\s*\n", " ", text)
    # Bash and PowerShell copies commonly use backtick/backslash line continuation.
    text = re.sub(r"\s*`\\?\s*\n", " ", text)
    text = re.sub(r"\s*\\\s*\n", " ", text)
    return text


def split_command_args(raw_text: str) -> list[str]:
    normalized = normalize_copied_command(raw_text)
    try:
        return shlex.split(normalized, posix=True)
    except ValueError:
        return shlex.split(normalized.replace('"', '\\"'), posix=False)


def header_from_arg(value: str) -> tuple[str, str] | None:
    value = _strip_wrapping_quotes(value)
    if ":" not in value:
        return None
    key, header_value = value.split(":", 1)
    key = key.strip()
    if not key:
        return None
    return key, header_value.strip()


def should_send_header(name: str) -> bool:
    lowered = (name or "").strip().lower()
    if lowered in SKIP_HEADER_NAMES:
        return False
    if lowered.startswith(SKIP_HEADER_PREFIXES):
        return False
    return True


def header_rows_from_headers(headers: dict[str, str]) -> list[dict]:
    return [
        {
            "enabled": should_send_header(name),
            "name": name,
            "value": value,
        }
        for name, value in headers.items()
    ]


def headers_from_header_rows(rows: list[dict]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for row in rows or []:
        if not row.get("enabled", True):
            continue
        name = str(row.get("name") or "").strip()
        value = str(row.get("value") or "")
        if name:
            headers[name] = value
    return headers


def infer_body_type(headers: dict[str, str], body: str) -> str:
    body = (body or "").strip()
    content_type = ""
    for key, value in (headers or {}).items():
        if key.lower() == "content-type":
            content_type = value.lower()
            break
    if "application/json" in content_type:
        return "json"
    if "application/x-www-form-urlencoded" in content_type:
        return "form"
    if "multipart/form-data" in content_type:
        return "raw"
    if not body:
        return "form"
    try:
        parsed = json.loads(body)
        if isinstance(parsed, (dict, list)):
            return "json"
    except json.JSONDecodeError:
        pass
    query_candidate = body[1:] if body.startswith("?") else body
    if "=" in query_candidate and ("\n" not in query_candidate or "&" in query_candidate):
        return "form"
    return "raw"


def parse_body_payload(body: str, body_type: str) -> tuple[object, str]:
    body = body or ""
    stripped = body.strip()
    if body_type == "json":
        if not stripped:
            return {}, ""
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            # Some browser/tools copy JSON as an escaped snippet: {\"a\":\"b\"}.
            if '\\"' in stripped:
                parsed = json.loads(stripped.replace('\\"', '"'))
            else:
                raise
        return parsed, ""
    if body_type == "form":
        if not stripped:
            return {}, ""
        query_candidate = stripped[1:] if stripped.startswith("?") else stripped
        payload: dict = {}
        if "=" in query_candidate and ("\n" not in query_candidate or "&" in query_candidate):
            for key, value in parse_qsl(query_candidate, keep_blank_values=True):
                _append_payload_value(payload, key, value)
            return payload, ""
        for line in stripped.splitlines():
            line = line.strip().strip(",")
            if not line:
                continue
            if "=" in line:
                key, value = line.split("=", 1)
            elif ":" in line:
                key, value = line.split(":", 1)
            else:
                continue
            key = key.strip().strip('"').strip("'")
            value = value.strip().strip('"').strip("'")
            if key:
                _append_payload_value(payload, key, value)
        if payload:
            return payload, ""
    return {}, body


def merge_url_query_payload(url: str, payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    merged = dict(payload or {})
    parsed_url = urlparse((url or "").strip())
    for key, value in parse_qsl(parsed_url.query, keep_blank_values=True):
        if key not in merged:
            merged[key] = value
    return merged


def absolute_url_from_request_target(target: str, headers: dict[str, str]) -> str:
    target = (target or "").strip()
    if target.startswith(("http://", "https://")):
        return target
    host = ""
    for key, value in headers.items():
        if key.lower() == "host":
            host = value.strip()
            break
    if not host:
        return target
    scheme = "https"
    return f"{scheme}://{host}{target if target.startswith('/') else '/' + target}"


def split_raw_http_request(raw_request: str) -> tuple[str, str, str, dict[str, str], str]:
    raw = (raw_request or "").replace("\r\n", "\n")
    header_part, separator, body = raw.partition("\n\n")
    if not separator:
        header_part = raw
        body = ""
    lines = header_part.splitlines()
    method = "POST"
    target = ""
    header_lines = lines
    if lines:
        first = lines[0].strip()
        match = re.match(r"^([A-Za-z]+)\s+(\S+)(?:\s+HTTP/\d(?:\.\d)?)?$", first)
        if match and match.group(1).upper() in HTTP_METHODS:
            method = match.group(1).upper()
            target = match.group(2)
            header_lines = lines[1:]
    headers = parse_headers_text("\n".join(header_lines))
    url = absolute_url_from_request_target(target, headers) if target else ""
    return method, url, "\n".join(header_lines), headers, body


def parse_curl_request(raw_request: str) -> dict | None:
    text = normalize_copied_command(raw_request)
    if not re.match(r"^(curl|curl\.exe)\b", text, flags=re.I):
        return None
    args = split_command_args(text)
    headers: dict[str, str] = {}
    method = ""
    url = ""
    body = ""
    index = 1
    while index < len(args):
        arg = args[index]
        next_value = args[index + 1] if index + 1 < len(args) else ""
        lowered = arg.lower()
        if lowered in {"-x", "--request"} and next_value:
            method = next_value.upper()
            index += 2
            continue
        if lowered.startswith("-x") and len(arg) > 2:
            method = arg[2:].strip().upper()
            index += 1
            continue
        if lowered in {"-h", "--header"} and next_value:
            parsed_header = header_from_arg(next_value)
            if parsed_header:
                headers[parsed_header[0]] = parsed_header[1]
            index += 2
            continue
        if lowered in {"--data", "--data-raw", "--data-binary", "--data-urlencode", "-d"} and next_value:
            body = next_value
            method = method or "POST"
            index += 2
            continue
        if lowered.startswith("http://") or lowered.startswith("https://"):
            url = _strip_wrapping_quotes(arg)
        index += 1
    return {
        "method": method or ("POST" if body else "GET"),
        "url": url,
        "headers": headers,
        "body": body,
    }


def _parse_js_object_like(value: str):
    value = (value or "").strip().rstrip(",")
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    normalized = re.sub(r"([{,]\s*)([A-Za-z_$][\w$-]*)(\s*:)", r'\1"\2"\3', value)
    normalized = normalized.replace("'", '"')
    try:
        return json.loads(normalized)
    except json.JSONDecodeError:
        return None


def extract_quoted_value(text: str, start_index: int) -> str:
    if start_index < 0 or start_index >= len(text) or text[start_index] not in {"'", '"'}:
        return ""
    quote = text[start_index]
    chars = []
    escaped = False
    for char in text[start_index + 1:]:
        if escaped:
            chars.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            chars.append(char)
            continue
        if char == quote:
            break
        chars.append(char)
    return "".join(chars)


def parse_fetch_request(raw_request: str) -> dict | None:
    text = (raw_request or "").strip()
    if "fetch(" not in text:
        return None
    url_match = re.search(r"fetch\(\s*(['\"])(.*?)\1", text, flags=re.S)
    url = url_match.group(2) if url_match else ""
    method_match = re.search(r"\bmethod\s*:\s*(['\"])(.*?)\1", text, flags=re.S)
    method = method_match.group(2).upper() if method_match else "GET"
    headers: dict[str, str] = {}
    headers_match = re.search(r"\bheaders\s*:\s*(\{.*?\})\s*,\s*(?:body|method|mode|credentials|referrer|$)", text, flags=re.S)
    if headers_match:
        parsed_headers = _parse_js_object_like(headers_match.group(1))
        if isinstance(parsed_headers, dict):
            headers = {str(key): str(value) for key, value in parsed_headers.items()}
    body = ""
    body_key_match = re.search(r"\bbody\s*:\s*", text, flags=re.S)
    if body_key_match:
        body = extract_quoted_value(text, body_key_match.end())
        body = body.replace('\\"', '"')
    return {
        "method": method or ("POST" if body else "GET"),
        "url": url,
        "headers": headers,
        "body": body,
    }


def parse_powershell_request(raw_request: str) -> dict | None:
    text = normalize_copied_command(raw_request)
    if not re.search(r"\b(Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b", text, flags=re.I):
        return None
    headers: dict[str, str] = {}
    uri_match = re.search(r"-(?:Uri|UseBasicParsing\s+-Uri)\s+(['\"])(.*?)\1", text, flags=re.I | re.S)
    if not uri_match:
        uri_match = re.search(r"\s(https?://\S+)", text, flags=re.I)
    url = uri_match.group(2) if uri_match and len(uri_match.groups()) >= 2 else (uri_match.group(1) if uri_match else "")
    method_match = re.search(r"-Method\s+([A-Za-z]+)", text, flags=re.I)
    method = method_match.group(1).upper() if method_match else "GET"
    headers_match = re.search(r"-Headers\s+@\{(.*?)\}", text, flags=re.I | re.S)
    if headers_match:
        for key, _, value in re.findall(r"['\"]?([^='\";]+)['\"]?\s*=\s*(['\"])(.*?)\2", headers_match.group(1), flags=re.S):
            headers[key.strip()] = value.strip()
    body_match = re.search(r"-Body\s+(['\"])(.*?)\1", text, flags=re.I | re.S)
    body = body_match.group(2) if body_match else ""
    return {
        "method": method or ("POST" if body else "GET"),
        "url": url,
        "headers": headers,
        "body": body,
    }


def parse_har_request(raw_request: str) -> dict | None:
    text = (raw_request or "").strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    entries = (((payload.get("log") or {}).get("entries")) or [])
    if not entries:
        request = payload.get("request") if isinstance(payload.get("request"), dict) else None
    else:
        request = (entries[0] or {}).get("request")
    if not isinstance(request, dict):
        return None
    headers = {
        item.get("name"): item.get("value", "")
        for item in request.get("headers", [])
        if item.get("name")
    }
    post_data = request.get("postData") or {}
    body = post_data.get("text") or ""
    return {
        "method": (request.get("method") or "GET").upper(),
        "url": request.get("url") or "",
        "headers": headers,
        "body": body,
    }


def parse_copied_request(raw_request: str) -> dict | None:
    for parser in (parse_har_request, parse_curl_request, parse_fetch_request, parse_powershell_request):
        parsed = parser(raw_request)
        if parsed:
            return parsed
    return None


def parse_request_by_mode(
    mode: str,
    raw_request: str = "",
    method: str = "",
    url: str = "",
    headers_text: str = "",
    body: str = "",
) -> dict:
    mode = (mode or "auto").strip().lower()
    copied = None
    if mode == "auto":
        return parse_request_parts(raw_request, method, url, headers_text, body)
    if mode == "curl":
        copied = parse_curl_request(raw_request)
    elif mode == "powershell":
        copied = parse_powershell_request(raw_request)
    elif mode == "fetch":
        copied = parse_fetch_request(raw_request)
    elif mode == "har":
        copied = parse_har_request(raw_request)
    elif mode == "raw_http":
        parsed_method, parsed_url, _, parsed_headers, parsed_body = split_raw_http_request(raw_request)
        copied = {
            "method": parsed_method,
            "url": parsed_url,
            "headers": parsed_headers,
            "body": parsed_body,
        }
    elif mode == "headers_body":
        copied = {
            "method": (method or "POST").upper(),
            "url": url or "",
            "headers": parse_headers_text(headers_text or raw_request),
            "body": body,
        }
    else:
        raise ValueError(f"不支持的解析方式: {mode}")

    if copied is None:
        raise ValueError(f"复制内容不像 {mode} 格式，请确认解析方式是否选对")

    merged_method = copied.get("method") or method or "POST"
    merged_url = copied.get("url") or url or ""
    headers = copied.get("headers") or parse_headers_text(headers_text)
    merged_body = copied.get("body") or body
    body_type = infer_body_type(headers, merged_body)
    data, raw_body = parse_body_payload(merged_body, body_type)
    data = merge_url_query_payload(merged_url, data)
    return {
        "method": merged_method,
        "url": merged_url,
        "headers": headers,
        "header_rows": header_rows_from_headers(headers),
        "body_type": body_type,
        "data": data if body_type in {"json", "form"} else {},
        "raw_body": raw_body if body_type == "raw" else "",
    }


def parse_request_parts(
    raw_request: str = "",
    method: str = "",
    url: str = "",
    headers_text: str = "",
    body: str = "",
) -> dict:
    if (raw_request or "").strip():
        copied = parse_copied_request(raw_request)
        if copied:
            method = copied.get("method") or method
            url = copied.get("url") or url
            headers = copied.get("headers") or parse_headers_text(headers_text)
            body = copied.get("body") or body
        else:
            parsed_method, parsed_url, _, parsed_headers, parsed_body = split_raw_http_request(raw_request)
            method = parsed_method
            url = parsed_url or url
            headers = parsed_headers
            body = parsed_body or body
    else:
        headers = parse_headers_text(headers_text)
        method = (method or "POST").upper()
    body_type = infer_body_type(headers, body)
    data, raw_body = parse_body_payload(body, body_type)
    data = merge_url_query_payload(url, data)
    return {
        "method": method or "POST",
        "url": url or "",
        "headers": headers,
        "header_rows": header_rows_from_headers(headers),
        "body_type": body_type,
        "data": data if body_type in {"json", "form"} else {},
        "raw_body": raw_body if body_type == "raw" else "",
    }
