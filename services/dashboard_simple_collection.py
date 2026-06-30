"""Simplified collection without any report configuration.

All parameters are defined in code or from database tables.
"""

from __future__ import annotations

import threading
import time
from datetime import date
from typing import Any, Callable

import requests

from services.dashboard_collection_service import CollectionTarget
from services.method_service import (
    build_cookie_jar,
    build_headers,
    raise_for_status_with_context,
    response_json_with_context,
    request_report,
)


# ============== 固定配置 ==============

REALTIME_URL = "https://usm.ha.cmcc:19011/dszzCombat/dszzRestful/combatreal/getDetailByAreaAndIndex"
ACC_URL = "https://usm.ha.cmcc:19011/dszzCombat/dszzRestful/combatreal/getDetailsByDateAndArea"

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://usm.ha.cmcc:19011",
    "Referer": "https://usm.ha.cmcc:19011/dszzCombat/dszzWeb/h5combatplatform/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
}

DEFAULT_TIMEOUT = 30


# ============== 请求参数构造 ==============

def build_request_params(
    target_code: str,
    indicator_codes: list[str],
    query_date: date,
    period_type: str = "DAY_ACC",
) -> dict[str, Any]:
    """构建请求参数"""
    normalized_period = period_type.upper()
    codes = ",".join(indicator_codes)

    payload = {
        "areaId": target_code,
        "indCodes": codes,
        "diyCodes": codes,
        "areaType": None,
    }

    if normalized_period == "REALTIME":
        payload["queryDate"] = query_date.strftime("%Y%m%d")
        return payload

    if normalized_period == "MONTH":
        payload["queryDate"] = query_date.strftime("%Y%m")
        payload["indType"] = "M"
        return payload

    payload["queryDate"] = query_date.strftime("%Y%m%d")
    payload["indType"] = "DD"
    return payload


# ============== 采集器 ==============

def create_simple_fetcher(
    stage: dict[str, Any],
    indicator_codes: list[str],
    query_date: date,
    period_type: str = "DAY_ACC",
    timeout_seconds: int = DEFAULT_TIMEOUT,
    request_retries: int = 2,
    retry_delay_seconds: float = 0.5,
) -> Callable[[Any], dict[str, Any]]:
    """创建简化的数据采集器

    不需要任何报表配置，直接发送请求
    """
    thread_local = threading.local()

    if not indicator_codes:
        raise ValueError("没有启用的驾驶舱指标")

    # 预先校验动态请求头依赖，避免批量请求阶段才整体失败。
    build_headers(
        {
            "headers": HEADERS,
            "headers_from_session_storage": {
                "Uaptoken": "uapToken",
            },
        },
        stage,
    )

    def fetch(target: CollectionTarget) -> dict[str, Any]:
        session = getattr(thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            session.cookies.update(build_cookie_jar(stage))
            thread_local.session = session

        params = build_request_params(
            target_code=target.target_code,
            indicator_codes=indicator_codes,
            query_date=query_date,
            period_type=period_type,
        )

        request_url = REALTIME_URL if period_type.upper() == "REALTIME" else ACC_URL

        payload = {
            "url": request_url,
            "method": "POST",
            "headers": HEADERS,
            "headers_from_session_storage": {
                "Uaptoken": "uapToken",
            },
            "body_type": "json",
            "data": params,
        }

        for attempt in range(request_retries + 1):
            response = request_report(
                session,
                payload,
                stage,
                timeout_seconds,
                False,
                None,
                retry={
                    "retries": request_retries,
                    "delay_seconds": retry_delay_seconds,
                    "backoff": 2,
                },
            )
            raise_for_status_with_context(response)
            result = response_json_with_context(response)
            response_code = str(result.get("reCode") or "").strip()
            if response_code not in {"1104"} or attempt >= request_retries:
                return result
            wait_seconds = retry_delay_seconds * (2**attempt)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        raise AssertionError("unreachable")

    return fetch
