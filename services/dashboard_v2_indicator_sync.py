"""Synchronize the upstream indicator catalogue into Dashboard V2."""

from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from infrastructure.dashboard_mysql import create_dashboard_engine, dashboard_mysql_lock
from infrastructure.dashboard_v2_run_store import MySQLV2CollectionRunStore
from models.dashboard_v2 import CollectionRunV2
from services.dashboard_indicator_sync import (
    _extract_indicator_records,
    fetch_user_diy_indicators,
)
from services.dashboard_v2_trigger import (
    build_city_ops_login_config,
    generate_batch_no,
    load_dashboard_config,
    normalize_trigger_type,
    now_shanghai,
    resolve_project_path,
    sanitize_error,
)
from services.dashboard_v2_indicator_service import sync_v2_indicator_records
from services.method_service import find_stage, load_json
from services.session_manager import file_lock, prepare_session


def execute_dashboard_v2_indicator_sync(
    config_path: str | Path = "config/dashboard/session.json",
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    event_logger: Any = None,
) -> dict[str, Any]:
    """Run V2 indicator sync without requiring an initialized hierarchy."""
    started = perf_counter()
    logger = event_logger or logging.getLogger(__name__)
    config, resolved_config_path = load_dashboard_config(config_path)
    trigger = normalize_trigger_type(trigger_type)
    batch_no = batch_no or generate_batch_no().replace(
        "dashboard-", "dashboard-v2-indicators-", 1
    )
    lock_path = resolve_project_path(
        config.get("collection_lock_path", "runtime/session/locks/dashboard_collection.lock")
    )
    engine = create_dashboard_engine()
    run_store = MySQLV2CollectionRunStore(engine)
    created = False
    try:
        with file_lock(
            lock_path,
            wait_seconds=float(config.get("collection_lock_wait_seconds", 5) or 5),
            poll_seconds=float(config.get("collection_lock_poll_seconds", 1) or 1),
            stale_seconds=float(
                config.get("collection_lock_stale_seconds", 1800) or 1800
            ),
            lock_label="V2 指标库同步锁",
        ) as local_lock:
            run_store.create(batch_no, trigger, run_type="INDICATOR_SYNC")
            created = True
            run_store.update(batch_no, status="RUNNING", phase="SESSION")
            login_config = build_city_ops_login_config(
                config, resolved_config_path.parent
            )
            session_result = prepare_session(
                login_config,
                base_dir=resolve_project_path("."),
                force_refresh=force_refresh,
                event_logger=event_logger,
            )
            if session_result.get("status") == "invalid":
                raise RuntimeError("V2 指标同步 city_ops 会话不可用")
            cookie_path = session_result.get("cookie_dump_path")
            if not cookie_path:
                raise RuntimeError("V2 指标同步未返回 cookie_dump_path")
            stage = find_stage(
                load_json(resolve_project_path(cookie_path)),
                str(config.get("required_stage") or "city_ops"),
            )
            with dashboard_mysql_lock(
                engine,
                lock_name=str(
                    config.get("collection_database_lock_name")
                    or "auto_notify_dashboard_collection"
                ),
                wait_seconds=int(
                    config.get("collection_database_lock_wait_seconds", 5) or 5
                ),
                heartbeat_seconds=int(
                    config.get("collection_database_lock_heartbeat_seconds", 30)
                    or 0
                ),
                idle_timeout_seconds=int(
                    config.get("collection_database_lock_idle_timeout_seconds", 300)
                    or 0
                ),
            ) as database_lock:
                payload = fetch_user_diy_indicators(
                    stage,
                    timeout_seconds=int(
                        config.get("indicator_sync_timeout_seconds")
                        or config.get("collection_timeout_seconds", 30)
                        or 30
                    ),
                )
                records = _extract_indicator_records(payload)
                finished_at = now_shanghai().replace(tzinfo=None)
                with Session(engine) as session, session.begin():
                    result = sync_v2_indicator_records(session, records)
                    run = session.scalar(
                        select(CollectionRunV2)
                        .where(CollectionRunV2.batch_no == batch_no)
                        .with_for_update()
                    )
                    if run is None:
                        raise FileNotFoundError(f"V2 批次不存在: {batch_no}")
                    run.status = "SUCCESS"
                    run.phase = "COMPLETED"
                    run.finished_at = finished_at
                    run.session_status = session_result.get("status")
                    run.row_count = result["source_count"]
                    database_lock.assert_held()
                total_seconds = perf_counter() - started
                logger.info(
                    "V2 指标库同步完成 batch_no=%s source=%s inserted=%s total=%.3fs",
                    batch_no,
                    result["source_count"],
                    result["inserted_count"],
                    total_seconds,
                )
                return {
                    "batch_no": batch_no,
                    "status": "SUCCESS",
                    "phase": "COMPLETED",
                    "trigger_type": trigger,
                    "session_status": session_result.get("status"),
                    "period_type": "INDICATOR_SYNC",
                    "timing": {"total_seconds": round(total_seconds, 3)},
                    "lock": {
                        **local_lock,
                        "database_lock": database_lock.as_dict(),
                    },
                    **result,
                }
    except Exception as exc:
        if created:
            try:
                run_store.update(
                    batch_no,
                    status="FAILED",
                    phase=str(getattr(exc, "phase", "INDICATOR_SYNC")),
                    finished_at=now_shanghai().replace(tzinfo=None),
                    error_type=str(getattr(exc, "error_type", type(exc).__name__)),
                    error_message={"message": sanitize_error(exc)},
                )
            except Exception:
                logger.exception("V2 指标同步失败状态回写失败")
        raise
    finally:
        run_store.close()
        engine.dispose()
