"""Sync dashboard indicator definitions from the city-ops DIY index API."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from infrastructure.dashboard_mysql import create_dashboard_engine
from models.dashboard_indicator import Indicator
from services.dashboard_trigger import (
    execute_session_phase,
    generate_batch_no,
    load_dashboard_config,
    resolve_project_path,
)
from services.method_service import (
    build_cookie_jar,
    build_headers,
    find_stage,
    load_json,
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


def _extract_indicator_records(payload: dict[str, Any]) -> list[tuple[str, str, int]]:
    response_code = str(payload.get("reCode") or "").strip()
    if response_code != "0000":
        message = str(payload.get("reMsg") or payload.get("message") or "")
        raise ValueError(f"指标清单接口返回失败: reCode={response_code}, reMsg={message}")

    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise ValueError("指标清单接口 result 不是对象，拒绝归档现有指标")

    records: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    source_order = 0
    for _field_name, items in result.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            code = str(item.get("indCode") or "").strip()
            name = str(item.get("indName") or "").strip()
            if not code or not name or code in seen:
                continue
            source_order += 1
            seen.add(code)
            records.append((code, name, source_order))
    if not records:
        raise ValueError("指标清单接口 result 中没有可用指标，拒绝归档现有指标")
    return records


def fetch_user_diy_indicators(
    stage: dict[str, Any],
    *,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Fetch the raw user DIY indicator payload using the city-ops session."""
    session = requests.Session()
    session.trust_env = False
    session.cookies.update(build_cookie_jar(stage))
    payload = {
        "url": INDICATOR_SYNC_URL,
        "method": "POST",
        "headers": INDICATOR_SYNC_HEADERS,
        "headers_from_session_storage": {
            "Uaptoken": "uapToken",
        },
        "body_type": "json",
        "data": {},
    }
    # Validate storage-derived headers before sending the request so failures
    # point at the session stage instead of looking like platform errors.
    build_headers(payload, stage)
    response = request_report(
        session,
        payload,
        stage,
        timeout_seconds,
        False,
        None,
    )
    raise_for_status_with_context(response)
    return response_json_with_context(response)


def sync_indicator_records(
    session: Session,
    records: list[tuple[str, str, int]],
) -> dict[str, Any]:
    """Upsert indicator code/name/sort_order from the latest API result.

    New indicators are inserted disabled. Existing ``enabled`` flags are kept
    intact. Indicators missing from the latest source list are archived so
    their historical metric rows remain queryable.
    """
    if not records:
        raise ValueError("指标清单为空，拒绝归档现有指标")

    codes = [code for code, _name, _order in records]
    existing = {
        row.code: row
        for row in session.scalars(
            select(Indicator).where(Indicator.code.in_(codes))
        ).all()
    }
    existing_codes = set(existing)
    max_sort = (
        session.scalar(select(Indicator.sort_order).order_by(Indicator.sort_order.desc()).limit(1))
        or 0
    )

    inserted: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    for code, name, source_order in records:
        indicator = existing.get(code)
        if indicator is None:
            session.add(
                Indicator(
                    code=code,
                    name=name,
                    enabled=False,
                    source_active=True,
                    sort_order=max_sort + source_order,
                )
            )
            inserted.append(code)
            continue

        changed = False
        if indicator.name != name:
            indicator.name = name
            changed = True
        if indicator.sort_order != source_order:
            indicator.sort_order = source_order
            changed = True
        if not indicator.source_active:
            indicator.source_active = True
            changed = True
        if indicator.removed_at is not None:
            indicator.removed_at = None
            changed = True
        if changed:
            updated.append(code)
        else:
            unchanged.append(code)

    archived_missing: list[str] = []
    archived_at = datetime.now()
    for indicator in session.scalars(
        select(Indicator).where(
            Indicator.source_active.is_(True),
            Indicator.enabled.is_(False),
            Indicator.code.not_in(codes),
        )
    ).all():
        indicator.source_active = False
        indicator.removed_at = archived_at
        archived_missing.append(indicator.code)

    session.flush()
    total = session.scalar(select(func.count()).select_from(Indicator))
    enabled = session.scalar(
        select(func.count()).select_from(Indicator).where(Indicator.enabled.is_(True))
    )
    source_active = session.scalar(
        select(func.count())
        .select_from(Indicator)
        .where(Indicator.source_active.is_(True))
    )
    return {
        "source_count": len(records),
        "inserted_count": len(inserted),
        "updated_count": len(updated),
        "unchanged_count": len(unchanged),
        "archived_missing_count": len(archived_missing),
        "indicator_total": int(total or 0),
        "indicator_enabled": int(enabled or 0),
        "indicator_source_active": int(source_active or 0),
        "indicator_archived": int(total or 0) - int(source_active or 0),
        "inserted_codes": inserted[:50],
        "updated_codes": updated[:50],
        "archived_missing_codes": archived_missing[:50],
        "existing_matched_count": len(existing_codes),
    }


def execute_dashboard_indicator_sync(
    config_path: str | Path = "config/dashboard/session.json",
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    event_logger: Any = None,
) -> dict[str, Any]:
    """Refresh the dashboard indicator library from all indicator lists once daily."""
    logger = event_logger or logging.getLogger(__name__)
    started = perf_counter()
    dashboard_config, _resolved = load_dashboard_config(config_path)
    batch_no = batch_no or generate_batch_no().replace(
        "dashboard-",
        "dashboard-indicators-",
        1,
    )
    session_result = execute_session_phase(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        batch_no=batch_no,
        event_logger=event_logger,
        acquire_collection_lock=True,
        run_type="REALTIME",
    )
    cookie_dump_path = session_result.get("cookie_dump_path")
    if not cookie_dump_path:
        raise RuntimeError("会话阶段未返回 cookie_dump_path")
    stage = find_stage(
        load_json(resolve_project_path(cookie_dump_path)),
        str(dashboard_config.get("required_stage") or "city_ops"),
    )

    timeout_seconds = int(
        dashboard_config.get("indicator_sync_timeout_seconds")
        or dashboard_config.get("collection_timeout_seconds", 30)
        or 30
    )
    payload = fetch_user_diy_indicators(stage, timeout_seconds=timeout_seconds)
    records = _extract_indicator_records(payload)

    engine = create_dashboard_engine()
    try:
        with Session(engine) as db_session:
            with db_session.begin():
                sync_result = sync_indicator_records(
                    db_session,
                    records,
                )
    finally:
        engine.dispose()

    total_seconds = perf_counter() - started
    logger.info(
        "驾驶舱指标库同步完成 batch_no=%s source=%s inserted=%s updated=%s enabled=%s total=%.3fs",
        batch_no,
        sync_result["source_count"],
        sync_result["inserted_count"],
        sync_result["updated_count"],
        sync_result["indicator_enabled"],
        total_seconds,
    )
    return {
        "batch_no": batch_no,
        "status": "SUCCESS",
        "phase": "INDICATOR_SYNC",
        "trigger_type": session_result["trigger_type"],
        "session_status": session_result.get("session_status"),
        "period_type": "INDICATOR_SYNC",
        "timing": {"total_seconds": round(total_seconds, 3)},
        **sync_result,
    }
