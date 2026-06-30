"""End-to-end Dashboard V2 collection pipeline."""

from __future__ import annotations

import logging
import random
import time
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from models.dashboard_v2 import CollectionRunV2
from services.dashboard_custom_indicator_service import compose_store_metric_rows
from services.dashboard_simple_collection import create_simple_fetcher
from services.dashboard_trigger import generate_batch_no, now_shanghai
from services.dashboard_v2_batch_runner import dashboard_v2_batch
from services.dashboard_v2_hierarchy import (
    attach_v2_node_ids_in_session,
    removed_identities_from_change_plan,
    sync_v2_hierarchy_in_session,
)
from services.dashboard_v2_metric_store import (
    finalize_v2_run_in_session,
    normalize_v2_metric_rows,
    write_v2_acc_metrics_in_session,
    write_v2_realtime_metrics_in_session,
)


MYSQL_RETRYABLE_ERROR_CODES = {1205, 1213}


def execute_dashboard_v2_pipeline(
    config_path: str | Path = "config/dashboard/session.json",
    trigger_type: str = "SCHEDULED",
    force_refresh: bool = False,
    batch_no: str | None = None,
    stat_date: date | None = None,
    event_logger: Any = None,
    period_type: str = "REALTIME",
) -> dict[str, Any]:
    """Collect, validate, and atomically write one V2 batch."""
    normalized_period = str(period_type or "").strip().upper()
    if normalized_period not in {"REALTIME", "DAY_ACC", "MONTH"}:
        raise ValueError("period_type 只支持 REALTIME/DAY_ACC/MONTH")
    prefix = {
        "REALTIME": "dashboard-v2-",
        "DAY_ACC": "dashboard-v2-daily-",
        "MONTH": "dashboard-v2-monthly-",
    }[normalized_period]
    batch_no = batch_no or generate_batch_no().replace("dashboard-", prefix, 1)
    query_date = resolve_v2_query_date(normalized_period, stat_date)
    logger = event_logger or logging.getLogger(__name__)

    with dashboard_v2_batch(
        config_path=config_path,
        trigger_type=trigger_type,
        force_refresh=force_refresh,
        batch_no=batch_no,
        query_date=query_date,
        run_type=normalized_period,
        event_logger=event_logger,
    ) as batch:
        config = batch.config
        plan = batch.indicator_plan
        fetch_metrics = create_simple_fetcher(
            stage=batch.stage,
            indicator_codes=plan["request_codes"],
            query_date=query_date,
            period_type=normalized_period,
            timeout_seconds=int(config.get("collection_timeout_seconds", 30) or 30),
            request_retries=int(config.get("collection_request_retries", 2) or 0),
            retry_delay_seconds=float(
                config.get("collection_retry_delay_seconds", 0.5) or 0
            ),
        )
        orchestrated = batch.collect_validate(
            fetch_metrics,
            max_workers=int(config.get("collection_max_workers", 24) or 24),
            hard_limit=int(config.get("collection_hard_max_workers", 32) or 32),
            retry_strategy=str(
                config.get("collection_retry_strategy", "affected_grid")
                or "affected_grid"
            ),
        )
        write_result = _write_v2_transaction_with_retry(
            batch=batch,
            orchestrated=orchestrated,
            period_type=normalized_period,
            query_date=query_date,
            max_attempts=int(config.get("database_transaction_retries", 3) or 3),
            logger=logger,
        )
        return {
            **write_result,
            "trigger_type": trigger_type.upper(),
            "period_type": normalized_period,
            "query_date": query_date.isoformat(),
            "request_count": orchestrated["collection"]["request_count"],
            "node_count": orchestrated["validation"]["matched_node_count"],
            "attempts": orchestrated["attempts"],
            "attempt_timings": orchestrated["attempt_timings"],
            "structure_changed": orchestrated["structure_changed"],
            "structure_change_summary": orchestrated[
                "structure_change_summary"
            ],
            "lock": batch.lock_result,
        }


def resolve_v2_query_date(period_type: str, requested: date | None) -> date:
    if requested is not None:
        if period_type == "MONTH":
            return requested.replace(
                day=monthrange(requested.year, requested.month)[1]
            )
        return requested
    today = now_shanghai().date()
    if period_type == "REALTIME":
        return today
    if period_type == "DAY_ACC":
        return today - timedelta(days=1)
    current_month = today.replace(day=1)
    return current_month - timedelta(days=1)


def _write_v2_transaction_with_retry(
    *,
    batch: Any,
    orchestrated: dict[str, Any],
    period_type: str,
    query_date: date,
    max_attempts: int,
    logger: Any,
) -> dict[str, Any]:
    if not 1 <= max_attempts <= 5:
        raise ValueError("database_transaction_retries 必须在 1-5 之间")
    last_error: OperationalError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _write_v2_transaction(
                batch=batch,
                orchestrated=orchestrated,
                period_type=period_type,
                query_date=query_date,
                transaction_attempt=attempt,
            )
        except OperationalError as exc:
            if mysql_error_code(exc) not in MYSQL_RETRYABLE_ERROR_CODES:
                raise
            last_error = exc
            if attempt >= max_attempts:
                raise
            delay = min(2.0, 0.2 * (2 ** (attempt - 1))) + random.uniform(0, 0.1)
            logger.warning(
                "V2 MySQL 事务冲突重试 batch_no=%s attempt=%s code=%s delay=%.3f",
                batch.batch_no,
                attempt,
                mysql_error_code(exc),
                delay,
            )
            time.sleep(delay)
    if last_error is not None:
        raise last_error
    raise AssertionError("V2 MySQL 事务重试流程未返回")


def _write_v2_transaction(
    *,
    batch: Any,
    orchestrated: dict[str, Any],
    period_type: str,
    query_date: date,
    transaction_attempt: int,
) -> dict[str, Any]:
    collected_at = now_shanghai().replace(tzinfo=None)
    plan = batch.indicator_plan
    with Session(batch.engine) as session, session.begin():
        run = session.scalar(
            select(CollectionRunV2)
            .where(CollectionRunV2.batch_no == batch.batch_no)
            .with_for_update()
        )
        if run is None:
            raise FileNotFoundError(f"V2 批次不存在: {batch.batch_no}")
        run.structure_change_summary = orchestrated["structure_change_summary"]

        sync_result: dict[str, Any] = {"structure_changed": False}
        if orchestrated["structure_changed"]:
            sync_result = sync_v2_hierarchy_in_session(
                session,
                orchestrated["candidate_graph"],
                collected_at=collected_at,
                collection_run_id=run.id,
                removed_identities=removed_identities_from_change_plan(
                    orchestrated["change_plan"]
                ),
                missing_disable_threshold=int(
                    batch.config.get("relation_missing_disable_threshold", 2) or 2
                ),
            )
            sync_result["structure_changed"] = True

        rows = attach_v2_node_ids_in_session(
            session,
            orchestrated["validated_rows"],
        )
        rows = compose_store_metric_rows(rows, plan["custom_components"])
        normalized_by_indicator = {
            code: normalize_v2_metric_rows(
                [
                    {"node_id": row["node_id"], "raw_value": row.get(code)}
                    for row in rows
                ]
            )
            for code in plan["store_codes"]
        }

        if period_type == "REALTIME":
            metric_result = write_v2_realtime_metrics_in_session(
                session,
                batch_no=batch.batch_no,
                normalized_rows_by_indicator=normalized_by_indicator,
                stat_date=query_date,
                collected_at=collected_at,
            )
            current_count = metric_result["current_upsert_count"]
            snapshot_count = metric_result["snapshot_insert_count"]
            acc_count = 0
        else:
            metric_result = write_v2_acc_metrics_in_session(
                session,
                batch_no=batch.batch_no,
                normalized_rows_by_indicator=normalized_by_indicator,
                period_type=period_type,
                stat_date=query_date,
                collected_at=collected_at,
            )
            current_count = 0
            snapshot_count = 0
            acc_count = metric_result["acc_upsert_count"]

        finalize_v2_run_in_session(
            session,
            batch_no=batch.batch_no,
            stat_date=query_date,
            finished_at=collected_at,
            current_upsert_count=current_count,
            snapshot_insert_count=snapshot_count,
            acc_upsert_count=acc_count,
        )
    return {
        "batch_no": batch.batch_no,
        "status": "SUCCESS",
        "phase": "COMPLETED",
        "transaction_attempt": transaction_attempt,
        "sync": sync_result,
        **metric_result,
    }


def mysql_error_code(exc: OperationalError) -> int | None:
    original = exc.orig
    args = getattr(original, "args", None)
    return int(args[0]) if args else None
