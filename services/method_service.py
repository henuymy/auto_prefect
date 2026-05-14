"""Download configured report methods with exported cookies."""

from __future__ import annotations

import json
import re
import copy
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from urllib3.exceptions import InsecureRequestWarning

from services.json_excel_service import (
    drilldown_json_to_excel,
    get_by_path,
    json_response_to_excel,
    set_by_path,
)

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)


PROJECT_DIR = Path(__file__).resolve().parents[1]
AUTH_REDIRECT_KEYWORDS = (
    "login",
    "sso",
    "cas",
    "uac",
    "ticket",
)


def load_json(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(value, base_dir=PROJECT_DIR):
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def find_stage(cookie_dump, stage_name):
    for stage in cookie_dump.get("stages", []):
        if stage.get("stage") == stage_name:
            return stage
    available = ", ".join(stage.get("stage", "") for stage in cookie_dump.get("stages", []))
    raise KeyError(f"cookie_dump 中未找到 stage={stage_name!r}，可用 stage: {available}")


def build_cookie_jar(stage, cookie_names=None):
    jar = requests.cookies.RequestsCookieJar()
    allowed_names = set(cookie_names or [])
    for cookie in stage.get("cookies", []):
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        if allowed_names and name not in allowed_names:
            continue
        jar.set(
            name,
            value,
            domain=cookie.get("domain") or None,
            path=cookie.get("path") or "/",
        )
    return jar


def find_cookie_value(stage, cookie_name):
    for cookie in stage.get("cookies", []):
        if cookie.get("name") == cookie_name:
            return cookie.get("value")
    return None


def build_cookie_string(stage, cookie_names=None):
    allowed_names = set(cookie_names or [])
    pairs = []
    for cookie in stage.get("cookies", []):
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        if allowed_names and name not in allowed_names:
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def parse_storage_json(value):
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def get_nested_value(value, path_parts):
    current = parse_storage_json(value)
    for part in path_parts:
        if isinstance(current, str):
            current = parse_storage_json(current)
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def find_storage_value(stage, storage_path, storage_type="session_storage"):
    if not storage_path:
        return None
    storages = []
    if storage_type in {"session_storage", "session"}:
        storages.append(stage.get("session_storage") or {})
    elif storage_type in {"local_storage", "local"}:
        storages.append(stage.get("local_storage") or {})
    else:
        storages.extend([stage.get("session_storage") or {}, stage.get("local_storage") or {}])

    parts = str(storage_path).split(".")
    key = parts[0]
    nested_path = parts[1:]
    for storage in storages:
        if key not in storage:
            continue
        value = storage.get(key)
        if nested_path:
            value = get_nested_value(value, nested_path)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)
    return None


STORAGE_TOKEN_PATTERN = re.compile(r"\$\{(session_storage|local_storage):([^}]+)\}")


def resolve_storage_references(value, stage):
    if isinstance(value, dict):
        return {key: resolve_storage_references(item, stage) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_storage_references(item, stage) for item in value]
    if not isinstance(value, str):
        return value

    exact_match = STORAGE_TOKEN_PATTERN.fullmatch(value.strip())
    if exact_match:
        resolved = find_storage_value(stage, exact_match.group(2).strip(), exact_match.group(1))
        return resolved if resolved is not None else value

    def replace_match(match):
        resolved = find_storage_value(stage, match.group(2).strip(), match.group(1))
        return resolved if resolved is not None else match.group(0)

    return STORAGE_TOKEN_PATTERN.sub(replace_match, value)


def build_headers(report, stage):
    headers = resolve_storage_references(dict(report.get("headers") or {}), stage)
    for header_name, cookie_name in (report.get("headers_from_cookies") or {}).items():
        cookie_value = find_cookie_value(stage, cookie_name)
        if cookie_value:
            headers[header_name] = cookie_value
    for header_name, storage_path in (report.get("headers_from_session_storage") or {}).items():
        storage_value = find_storage_value(stage, storage_path, storage_type="session_storage")
        if not storage_value:
            raise RuntimeError(
                f"动态请求头 {header_name} 未能从 sessionStorage.{storage_path} 取到值，"
                "session 已过期或页面尚未写入 Storage"
            )
        headers[header_name] = storage_value
    for header_name, storage_path in (report.get("headers_from_local_storage") or {}).items():
        storage_value = find_storage_value(stage, storage_path, storage_type="local_storage")
        if not storage_value:
            raise RuntimeError(
                f"动态请求头 {header_name} 未能从 localStorage.{storage_path} 取到值，"
                "session 已过期或页面尚未写入 Storage"
            )
        headers[header_name] = storage_value
    for header_name, cookie_names in (report.get("headers_from_cookie_string") or {}).items():
        selected_names = None if cookie_names in (None, "*") else cookie_names
        if isinstance(selected_names, str):
            selected_names = [item.strip() for item in selected_names.split(",") if item.strip()]
        cookie_string = build_cookie_string(stage, selected_names)
        if not cookie_string:
            raise RuntimeError(f"动态请求头 {header_name} 未能从 Cookie 生成，session 已过期或 Cookie 为空")
        headers[header_name] = cookie_string
    return headers


def summarize_request(report, stage):
    headers = build_headers(report, stage)
    cookie_names = [
        cookie.get("name")
        for cookie in stage.get("cookies", [])
        if cookie.get("name") and (
            not report.get("cookie_names") or cookie.get("name") in set(report.get("cookie_names") or [])
        )
    ]
    return {
        "method": report["method"].upper(),
        "url": report.get("url"),
        "headers": sorted(headers.keys()),
        "cookies": cookie_names,
        "body_type": report["body_type"],
        "response_mode": report["response_mode"],
        "data_keys": list((report.get("data") or {}).keys()),
    }


def safe_filename(value):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip()
    return cleaned or f"report-{datetime.now().strftime('%Y%m%d%H%M%S')}.bin"


def filename_from_content_disposition(content_disposition, fallback_url, content_type=""):
    match = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, flags=re.I)
    if match:
        return safe_filename(unquote(match.group(1).strip()))
    match = re.search(r'filename="?([^";]+)"?', content_disposition, flags=re.I)
    if match:
        return safe_filename(unquote(match.group(1).strip()))

    parsed_url = urlparse(fallback_url)
    url_name = Path(parsed_url.path).name
    fallback_name = safe_filename(url_name or f"downloaded-report-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    if "." not in Path(fallback_name).name:
        lowered_type = content_type.lower()
        if "spreadsheetml" in lowered_type or "excel" in lowered_type or "octet-stream" in lowered_type:
            fallback_name = f"{fallback_name}.xlsx"
    return fallback_name


def filename_from_response(response):
    return filename_from_content_disposition(
        response.headers.get("Content-Disposition", ""),
        response.url,
        response.headers.get("Content-Type", ""),
    )


def output_filename_for_report(report, response_filename):
    report_name = safe_filename(report.get("name") or "")
    response_filename = safe_filename(response_filename)
    if not report_name:
        return response_filename
    if response_filename.startswith(f"{report_name}__"):
        return response_filename
    return f"{report_name}__{response_filename}"


def build_request_kwargs(report, stage, timeout, verify_ssl, proxies):
    body_type = report["body_type"].lower().strip()
    kwargs = {
        "headers": build_headers(report, stage),
        "params": resolve_storage_references(report.get("params"), stage) or None,
        "timeout": timeout,
        "verify": verify_ssl,
        "allow_redirects": bool(report.get("allow_redirects", False)),
    }
    if proxies:
        kwargs["proxies"] = proxies

    if body_type == "json":
        kwargs["json"] = resolve_storage_references(report.get("data", {}), stage)
    elif body_type == "raw":
        kwargs["data"] = resolve_storage_references(report.get("raw_body", ""), stage)
    elif body_type == "form":
        kwargs["data"] = resolve_storage_references(report.get("data", {}), stage)
    else:
        raise ValueError(f"body_type 只支持 form/json/raw: {body_type}")
    return kwargs


def request_report(session, report, stage, timeout, verify_ssl, proxies):
    method = report["method"].upper()
    url = report["url"]
    kwargs = build_request_kwargs(report, stage, timeout, verify_ssl, proxies)
    return session.request(method, url, **kwargs)


def response_json_with_context(response):
    try:
        return response.json()
    except ValueError as exc:
        content_type = response.headers.get("Content-Type", "")
        preview = response.text[:500].replace("\r", " ").replace("\n", " ")
        raise RuntimeError(
            f"下载响应不是 JSON，无法转 Excel: content_type={content_type}, body_preview={preview}"
        ) from exc


def raise_for_status_with_context(response):
    if 200 <= response.status_code < 300:
        return
    body_preview = response.text[:500].replace("\r", " ").replace("\n", " ")
    lowered_preview = body_preview.lower()
    location = response.headers.get("Location", "")
    lowered_location = location.lower()
    if response.status_code in {301, 302, 303, 307, 308}:
        if any(keyword in lowered_location for keyword in AUTH_REDIRECT_KEYWORDS):
            raise RuntimeError(
                "下载认证失效（session 已过期），需要重新登录后重试: "
                f"HTTP {response.status_code} {response.request.method} {response.url}; "
                f"location={location}; body_preview={body_preview}"
            )
        raise RuntimeError(
            "下载接口返回重定向但不是明确登录地址: "
            f"HTTP {response.status_code} {response.request.method} {response.url}; "
            f"location={location}; body_preview={body_preview}"
        )
    if response.status_code in {401, 403} or "unauthorized" in lowered_preview or "login.jsp" in lowered_preview:
        raise RuntimeError(
            "下载认证失效（session 已过期），需要重新登录后重试: "
            f"HTTP {response.status_code} {response.request.method} {response.url}; "
            f"body_preview={body_preview}"
        )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        details = {
            "status_code": response.status_code,
            "method": response.request.method,
            "url": response.url,
            "allow": response.headers.get("Allow"),
            "content_type": response.headers.get("Content-Type"),
            "location": location,
            "body_preview": body_preview,
        }
        raise RuntimeError(f"下载接口返回错误: {details}") from exc
    raise RuntimeError(f"下载接口返回非文件状态码: HTTP {response.status_code}")


def is_html_response(response):
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" in content_type:
        return True
    preview = response.content[:100].lower()
    return preview.lstrip().startswith(b"<!doctype html") or preview.lstrip().startswith(b"<html")


def download_one_report(report, stage, output_dir, timeout, verify_ssl, trust_env, proxies):
    session = requests.Session()
    session.trust_env = trust_env
    session.cookies.update(build_cookie_jar(stage, cookie_names=report.get("cookie_names")))
    session.headers.update({"User-Agent": "report-downloader/1.0"})
    response = request_report(session, report, stage, timeout, verify_ssl, proxies)
    raise_for_status_with_context(response)
    if is_html_response(response):
        raise RuntimeError(f"下载响应为 HTML（可能是登录页），session 已过期: {response.url}")
    if not response.content:
        raise RuntimeError(f"下载响应为空: HTTP {response.status_code} {response.url}")

    response_mode = str(report["response_mode"]).strip().lower()
    if response_mode in {"json_to_excel", "json_drilldown_to_excel"}:
        report_name = safe_filename(report.get("name") or "json-report")
        output_path = output_dir / f"{report_name}.xlsx"
        if response_mode == "json_to_excel":
            convert_result = json_response_to_excel(
                response_json_with_context(response),
                output_path,
                report.get("excel") or {},
            )
        else:
            initial_response_json = response_json_with_context(response)
            initial_payload = copy.deepcopy(report.get("data") or {})

            def fetch_next(payload_override):
                next_report = copy.deepcopy(report)
                if next_report.get("body_type") == "raw":
                    raise RuntimeError("json_drilldown_to_excel 不支持 raw 请求体")
                next_report["data"] = payload_override
                next_report.pop("json", None)
                next_response = request_report(session, next_report, stage, timeout, verify_ssl, proxies)
                raise_for_status_with_context(next_response)
                if is_html_response(next_response):
                    raise RuntimeError(f"下载响应为 HTML（可能是登录页），session 已过期: {next_response.url}")
                return response_json_with_context(next_response)

            request_area_field = (report.get("drilldown") or {}).get("request_area_field") or "areaId"
            if initial_payload:
                set_by_path(initial_payload, request_area_field, get_by_path(initial_payload, request_area_field))
            convert_result = drilldown_json_to_excel(
                initial_payload,
                fetch_next,
                output_path,
                report.get("drilldown") or {},
                report.get("excel") or {},
                initial_response_json=initial_response_json,
            )
        return {
            "name": report.get("name"),
            "stage": report.get("stage"),
            "transport": "requests",
            "response_mode": response_mode,
            "url": response.url,
            "status_code": response.status_code,
            "bytes": output_path.stat().st_size,
            "rows": convert_result.get("rows"),
            "requests": convert_result.get("requests", 1),
            "sheet_name": convert_result.get("sheet_name"),
            "output_path": str(output_path),
            "downloaded_at": datetime.now().isoformat(),
        }
    if response_mode != "file":
        raise ValueError(f"response_mode 只支持 file/json_to_excel/json_drilldown_to_excel: {response_mode}")

    response_filename = filename_from_response(response)
    filename = output_filename_for_report(report, response_filename)
    output_path = output_dir / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)
    return {
        "name": report.get("name"),
        "stage": report.get("stage"),
        "transport": "requests",
        "url": response.url,
        "status_code": response.status_code,
        "bytes": len(response.content),
        "response_filename": response_filename,
        "output_path": str(output_path),
        "downloaded_at": datetime.now().isoformat(),
    }


def write_manifest(path, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def download_reports(config, base_dir=PROJECT_DIR, dry_run=False, debug=False):
    cookie_dump_path = resolve_path(config.get("cookie_dump_path", "runtime/cookies/cookie_dump.json"), base_dir)
    output_dir = resolve_path(config.get("output_dir", "runtime/downloads"), base_dir)
    manifest_path = resolve_path(config.get("manifest_path", "runtime/download_manifest.json"), base_dir)
    timeout = int(config.get("request_timeout_seconds", 120))
    verify_ssl = bool(config.get("verify_ssl", True))
    trust_env = bool(config.get("trust_env", False))
    proxies = config.get("proxies") or None
    cookie_dump = load_json(cookie_dump_path)

    results = []
    reports = [report for report in config.get("reports", []) if report.get("enabled", True)]
    for report in reports:
        name = report.get("name", report.get("url", "未命名报表"))
        stage_name = report.get("stage")
        if not stage_name:
            raise ValueError(f"报表 {name} 缺少 stage")
        stage = find_stage(cookie_dump, stage_name)
        if dry_run:
            payload = {
                "name": name,
                "stage": stage_name,
                "url": report.get("url"),
                "dry_run": True,
            }
            if debug:
                payload["request_summary"] = summarize_request(report, stage)
            results.append(payload)
            continue
        report_proxies = report.get("proxies", proxies)
        results.append(download_one_report(report, stage, output_dir, timeout, verify_ssl, trust_env, report_proxies))

    manifest = {
        "generated_at": datetime.now().isoformat(),
        "cookie_dump_path": str(cookie_dump_path),
        "dry_run": dry_run,
        "results": results,
    }
    write_manifest(manifest_path, manifest)
    return manifest


def download_reports_from_config(config_path, base_dir=PROJECT_DIR, dry_run=False, debug=False):
    config_path = resolve_path(config_path, base_dir)
    config = load_json(config_path)
    return download_reports(config, base_dir=config_path.parent, dry_run=dry_run, debug=debug)
