"""City-ops indicator catalogue fetch helpers shared by Dashboard V2."""

from __future__ import annotations

from typing import Any

import requests

from services.method_service import (
    build_cookie_jar,
    build_headers,
    raise_for_status_with_context,
    request_report,
    response_json_with_context,
)


INDICATOR_SYNC_URL = (
    "https://usm.ha.cmcc:19011/dszzCombat/dszzRestful/combatreal/"
    "getUserDiyIndex"
)
INDICATOR_SYNC_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Content-Type": "application/json",
    "Origin": "https://usm.ha.cmcc:19011",
    "Pragma": "no-cache",
    "Referer": "https://usm.ha.cmcc:19011/dszzCombat/dszzWeb/h5combatplatform/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    ),
}


def extract_indicator_records(payload: dict[str, Any]) -> list[tuple[str, str, int]]:
    response_code = str(payload.get("reCode") or "").strip()
    if response_code != "0000":
        message = str(payload.get("reMsg") or payload.get("message") or "")
        raise ValueError(f"指标清单接口返回失败: reCode={response_code}, reMsg={message}")
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise ValueError("指标清单接口 result 不是对象，拒绝归档现有指标")
    records: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    for items in result.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            code = str(item.get("indCode") or "").strip()
            name = str(item.get("indName") or "").strip()
            if not code or not name or code in seen:
                continue
            seen.add(code)
            records.append((code, name, len(records) + 1))
    if not records:
        raise ValueError("指标清单接口 result 中没有可用指标，拒绝归档现有指标")
    return records


def fetch_user_diy_indicators(
    stage: dict[str, Any], *, timeout_seconds: int = 30
) -> dict[str, Any]:
    session = requests.Session()
    session.trust_env = False
    session.cookies.update(build_cookie_jar(stage))
    payload = {
        "url": INDICATOR_SYNC_URL,
        "method": "POST",
        "headers": INDICATOR_SYNC_HEADERS,
        "headers_from_session_storage": {"Uaptoken": "uapToken"},
        "body_type": "json",
        "data": {},
    }
    build_headers(payload, stage)
    response = request_report(session, payload, stage, timeout_seconds, False, None)
    raise_for_status_with_context(response)
    return response_json_with_context(response)
