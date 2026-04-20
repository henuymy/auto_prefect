"""Download configured report methods with exported cookies."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)


PROJECT_DIR = Path(__file__).resolve().parents[1]


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


def build_headers(report, stage):
    headers = dict(report.get("headers") or {})
    for header_name, cookie_name in (report.get("headers_from_cookies") or {}).items():
        cookie_value = find_cookie_value(stage, cookie_name)
        if cookie_value:
            headers[header_name] = cookie_value
    for header_cookie_name, value_cookie_name in (report.get("csrf_headers_from_cookies") or {}).items():
        header_name = find_cookie_value(stage, header_cookie_name)
        header_value = find_cookie_value(stage, value_cookie_name)
        if header_name and header_value:
            headers[header_name] = header_value
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
        "method": report.get("method", "GET").upper(),
        "url": report.get("url"),
        "headers": sorted(headers.keys()),
        "cookies": cookie_names,
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


def request_report(session, report, stage, timeout, verify_ssl, proxies):
    method = report.get("method", "GET").upper()
    url = report["url"]
    kwargs = {
        "headers": build_headers(report, stage),
        "params": report.get("params") or None,
        "timeout": timeout,
        "verify": verify_ssl,
        "allow_redirects": bool(report.get("allow_redirects", False)),
    }
    if proxies:
        kwargs["proxies"] = proxies
    if "json" in report:
        kwargs["json"] = report["json"]
    if "data" in report:
        kwargs["data"] = report["data"]
    return session.request(method, url, **kwargs)


def raise_for_status_with_context(response):
    if 200 <= response.status_code < 300:
        return
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        details = {
            "status_code": response.status_code,
            "method": response.request.method,
            "url": response.url,
            "allow": response.headers.get("Allow"),
            "content_type": response.headers.get("Content-Type"),
            "body_preview": response.text[:500].replace("\r", " ").replace("\n", " "),
        }
        raise RuntimeError(f"下载接口返回错误: {details}") from exc
    raise RuntimeError(f"下载接口返回非文件状态码: HTTP {response.status_code}")


def download_one_report(report, stage, output_dir, timeout, verify_ssl, trust_env, proxies):
    session = requests.Session()
    session.trust_env = trust_env
    session.cookies.update(build_cookie_jar(stage, cookie_names=report.get("cookie_names")))
    session.headers.update({"User-Agent": "report-downloader/1.0"})
    response = request_report(session, report, stage, timeout, verify_ssl, proxies)
    raise_for_status_with_context(response)
    if not response.content:
        raise RuntimeError(f"下载响应为空: HTTP {response.status_code} {response.url}")

    filename = filename_from_response(response)
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
