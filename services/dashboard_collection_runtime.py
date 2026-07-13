"""Dashboard V2 request collection primitives without a schema dependency."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable

import requests

from infrastructure.dashboard_run_protocol import CollectionRunStore
from services.json_excel_service import extract_rows
from services.method_service import (
    build_cookie_jar,
    build_headers,
    raise_for_status_with_context,
    request_report,
    response_json_with_context,
)


DEFAULT_MAX_WORKERS = 24
HARD_MAX_WORKERS = 32
FORMAL_REQUEST_TARGET_TYPES = {"CITY", "BRANCH", "GRID"}
SUCCESS_CODES = {"0", "0000"}
RETRYABLE_RESPONSE_CODES = {"1104"}
REALTIME_URL = "https://usm.ha.cmcc:19011/dszzCombat/dszzRestful/combatreal/getDetailByAreaAndIndex"
ACC_URL = "https://usm.ha.cmcc:19011/dszzCombat/dszzRestful/combatreal/getDetailsByDateAndArea"
HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://usm.ha.cmcc:19011",
    "Referer": "https://usm.ha.cmcc:19011/dszzCombat/dszzWeb/h5combatplatform/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
}


@dataclass(frozen=True)
class CollectionTarget:
    id: int
    target_code: str
    target_name: str
    target_type: str
    area_id: int | None
    parent_target_id: int | None
    sort_order: int


class DashboardCollectionError(RuntimeError):
    phase = "COLLECT"
    error_type = "COLLECTION_FAILED"

    def __init__(
        self,
        message: str,
        errors: list[dict[str, Any]] | None = None,
        response_code: str | None = None,
    ):
        self.errors = errors or []
        self.response_code = response_code
        super().__init__(message)


def normalize_max_workers(value: int | str | None, hard_limit: int = HARD_MAX_WORKERS) -> int:
    workers = int(value or DEFAULT_MAX_WORKERS)
    if workers <= 0:
        raise ValueError("采集并发数必须大于0")
    if hard_limit <= 0:
        raise ValueError("采集并发硬上限必须大于0")
    return min(workers, hard_limit)


def validate_payload(payload: dict[str, Any], target: CollectionTarget) -> None:
    response_code = payload.get("reCode")
    if response_code is not None and str(response_code).strip() not in SUCCESS_CODES:
        message = str(payload.get("reMsg") or payload.get("message") or "")[:200]
        raise DashboardCollectionError(
            f"平台返回失败: target={target.target_code}, reCode={response_code}, reMsg={message}",
            response_code=str(response_code),
        )


def extract_target_metric_rows(
    target: CollectionTarget,
    payload: dict[str, Any],
    indicator_codes: Iterable[str],
    *,
    include_manager_self_row: bool = False,
) -> list[dict[str, Any]]:
    validate_payload(payload, target)
    source_rows = extract_rows(payload, "result.tableData")
    codes = list(indicator_codes)
    if target.target_type in FORMAL_REQUEST_TARGET_TYPES:
        matching = [
            row
            for row in source_rows
            if str(row.get("areaCode") or "").strip() == target.target_code
        ]
        if len(matching) != 1:
            raise DashboardCollectionError(
                f"本级汇总行数量异常: target={target.target_type}/{target.target_code}, count={len(matching)}"
            )
        return [
            _metric_row(
                matching[0],
                level_type=target.target_type,
                parent_request_code=target.target_code,
                indicator_codes=codes,
            )
        ]
    if target.target_type == "CHANNEL_MANAGER":
        result = []
        for row in source_rows:
            area_code = str(row.get("areaCode") or "").strip()
            area_name = str(row.get("areaName") or "").strip()
            if not area_code or not area_name:
                continue
            if area_code == target.target_code:
                if include_manager_self_row:
                    result.append(
                        _metric_row(
                            row,
                            level_type="CHANNEL_MANAGER",
                            parent_request_code=target.target_code,
                            indicator_codes=codes,
                        )
                    )
                continue
            result.append(
                _metric_row(
                    row,
                    level_type="CHANNEL",
                    parent_request_code=target.target_code,
                    indicator_codes=codes,
                )
            )
        return result
    raise DashboardCollectionError(
        f"不支持的请求目标类型: {target.target_type}/{target.target_code}"
    )


def extract_structure_observations(
    target: CollectionTarget, payload: dict[str, Any]
) -> list[dict[str, str]]:
    if target.target_type not in {"GRID", "CHANNEL_MANAGER"}:
        return []
    child_type = "CHANNEL_MANAGER" if target.target_type == "GRID" else "CHANNEL"
    observations: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in extract_rows(payload, "result.tableData"):
        child_code = str(row.get("areaCode") or "").strip()
        child_name = str(row.get("areaName") or "").strip()
        if (
            not child_code
            or not child_name
            or child_code == target.target_code
            or child_code in seen
        ):
            continue
        seen.add(child_code)
        observations.append(
            {
                "parent_type": target.target_type,
                "parent_code": target.target_code,
                "child_type": child_type,
                "child_code": child_code,
                "child_name": child_name,
            }
        )
    return observations


def collect_metric_rows(
    targets: Iterable[CollectionTarget],
    indicator_codes: Iterable[str],
    fetch_payload: Callable[[CollectionTarget], dict[str, Any]],
    max_workers: int = DEFAULT_MAX_WORKERS,
    hard_limit: int = HARD_MAX_WORKERS,
    allow_recoverable_manager_failures: bool = False,
    include_manager_self_rows: bool = False,
) -> dict[str, Any]:
    target_list = list(targets)
    codes = list(indicator_codes)
    if not target_list:
        raise DashboardCollectionError("没有启用的请求目标")
    if not codes:
        raise DashboardCollectionError("没有启用的驾驶舱指标")
    workers = normalize_max_workers(max_workers, hard_limit)
    collected_by_target: dict[int, list[dict[str, Any]]] = {}
    structure_by_target: dict[int, list[dict[str, str]]] = {}
    errors: list[dict[str, str | None]] = []
    with ThreadPoolExecutor(
        max_workers=min(workers, len(target_list)),
        thread_name_prefix="dashboard-collect",
    ) as executor:
        futures = {executor.submit(fetch_payload, target): target for target in target_list}
        for future in as_completed(futures):
            target = futures[future]
            try:
                payload = future.result()
                collected_by_target[target.id] = extract_target_metric_rows(
                    target,
                    payload,
                    codes,
                    include_manager_self_row=include_manager_self_rows,
                )
                structure_by_target[target.id] = extract_structure_observations(target, payload)
            except Exception as exc:
                errors.append(
                    {
                        "target_type": target.target_type,
                        "target_code": target.target_code,
                        "response_code": getattr(exc, "response_code", None),
                        "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    }
                )
    recoverable_errors = [
        error
        for error in errors
        if error["target_type"] == "CHANNEL_MANAGER"
        and error.get("response_code") in RETRYABLE_RESPONSE_CODES
    ]
    if errors and (
        not allow_recoverable_manager_failures
        or len(recoverable_errors) != len(errors)
    ):
        raise DashboardCollectionError(
            f"{len(errors)}/{len(target_list)} 个请求目标采集失败，整批不得入库",
            errors=errors,  # type: ignore[arg-type]
        )
    rows = [row for target in target_list for row in collected_by_target.get(target.id, [])]
    observations = [
        row for target in target_list for row in structure_by_target.get(target.id, [])
    ]
    return {
        "rows": rows,
        "structure_observations": observations,
        "request_count": len(target_list),
        "row_count": len(rows),
        "max_workers": workers,
        "recoverable_errors": recoverable_errors,
    }


def execute_collection_phase(
    batch_no: str,
    targets: Iterable[CollectionTarget],
    indicator_codes: Iterable[str],
    fetch_payload: Callable[[CollectionTarget], dict[str, Any]],
    run_store: CollectionRunStore,
    max_workers: int = DEFAULT_MAX_WORKERS,
    hard_limit: int = HARD_MAX_WORKERS,
    now_provider: Callable[[], Any] | None = None,
    allow_recoverable_manager_failures: bool = False,
    include_manager_self_rows: bool = False,
) -> dict[str, Any]:
    target_list = list(targets)
    run_store.update(
        batch_no,
        status="RUNNING",
        phase="COLLECT",
        request_count=len(target_list),
        row_count=0,
        finished_at=None,
        error_type=None,
        error_message=None,
    )
    try:
        result = collect_metric_rows(
            target_list,
            indicator_codes,
            fetch_payload,
            max_workers=max_workers,
            hard_limit=hard_limit,
            allow_recoverable_manager_failures=allow_recoverable_manager_failures,
            include_manager_self_rows=include_manager_self_rows,
        )
    except DashboardCollectionError as exc:
        error_preview = "; ".join(
            f"{item['target_type']}/{item['target_code']}: {item['error']}"
            for item in exc.errors[:10]
        )
        run_store.update(
            batch_no,
            status="FAILED",
            phase=exc.phase,
            request_count=len(target_list),
            row_count=0,
            finished_at=now_provider() if now_provider else None,
            error_type=exc.error_type,
            error_message=f"{exc}; {error_preview}"[:2000],
        )
        raise
    run_store.update(
        batch_no,
        status="RUNNING",
        phase="COLLECTED",
        request_count=result["request_count"],
        row_count=result["row_count"],
    )
    return result


def create_simple_fetcher(
    stage: dict[str, Any],
    indicator_codes: list[str],
    query_date: date,
    period_type: str = "DAY_ACC",
    timeout_seconds: int = 30,
    request_retries: int = 2,
    retry_delay_seconds: float = 0.5,
) -> Callable[[CollectionTarget], dict[str, Any]]:
    if not indicator_codes:
        raise ValueError("没有启用的驾驶舱指标")
    build_headers(
        {"headers": HEADERS, "headers_from_session_storage": {"Uaptoken": "uapToken"}},
        stage,
    )
    thread_local = threading.local()

    def fetch(target: CollectionTarget) -> dict[str, Any]:
        session = getattr(thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            session.cookies.update(build_cookie_jar(stage))
            thread_local.session = session
        normalized_period = period_type.upper()
        params: dict[str, Any] = {
            "areaId": target.target_code,
            "indCodes": ",".join(indicator_codes),
            "diyCodes": ",".join(indicator_codes),
            "areaType": None,
        }
        if normalized_period == "REALTIME":
            params["queryDate"] = query_date.strftime("%Y%m%d")
        elif normalized_period == "MONTH":
            params.update({"queryDate": query_date.strftime("%Y%m"), "indType": "M"})
        else:
            params.update({"queryDate": query_date.strftime("%Y%m%d"), "indType": "DD"})
        payload = {
            "url": REALTIME_URL if normalized_period == "REALTIME" else ACC_URL,
            "method": "POST",
            "headers": HEADERS,
            "headers_from_session_storage": {"Uaptoken": "uapToken"},
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
            if str(result.get("reCode") or "").strip() not in RETRYABLE_RESPONSE_CODES or attempt >= request_retries:
                return result
            wait_seconds = retry_delay_seconds * (2**attempt)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        raise AssertionError("unreachable")

    return fetch


def _metric_row(
    source: dict[str, Any],
    *,
    level_type: str,
    parent_request_code: str,
    indicator_codes: list[str],
) -> dict[str, Any]:
    row = {
        "level_type": level_type,
        "area_code": str(source.get("areaCode") or "").strip(),
        "area_name": str(source.get("areaName") or "").strip(),
        "parent_request_code": parent_request_code,
    }
    for code in indicator_codes:
        row[code] = source.get(code)
    return row
