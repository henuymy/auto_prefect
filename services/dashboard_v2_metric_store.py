"""Atomic metric persistence for the Dashboard V2 schema."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from time import perf_counter
from typing import Any, Callable, Iterable

from sqlalchemy import case, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from models.dashboard_v2 import (
    CollectionRunV2,
    HierarchyNode,
    IndicatorV2,
    MetricAccV2,
    MetricCurrentV2,
    MetricSnapshotV2,
)
from services.dashboard_metrics import (
    MetricConflictError,
    MetricWriteError,
    parse_metric_value,
)


DEFAULT_MYSQL_WRITE_CHUNK_SIZE = 2_000
MAX_MYSQL_WRITE_CHUNK_SIZE = 10_000
SLOW_MYSQL_WRITE_CHUNK_SECONDS = 2.0

logger = logging.getLogger(__name__)


def normalize_v2_metric_rows(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate and deduplicate one indicator's node values."""
    deduplicated: dict[int, dict[str, Any]] = {}
    for source in rows:
        node_id = source.get("node_id")
        if not isinstance(node_id, int) or node_id <= 0:
            raise MetricWriteError("指标数据缺少有效 node_id")
        raw_value = (
            source["metric_value"]
            if "metric_value" in source
            else source.get("raw_value")
        )
        metric_value = parse_metric_value(raw_value)
        existing = deduplicated.get(node_id)
        if existing is not None:
            if existing["metric_value"] != metric_value:
                raise MetricConflictError(
                    f"同一节点同一指标出现冲突值: node_id={node_id}, "
                    f"{existing['metric_value']} != {metric_value}"
                )
            continue
        deduplicated[node_id] = {
            "node_id": node_id,
            "metric_value": metric_value,
        }
    if not deduplicated:
        raise MetricWriteError("没有可写入的有效指标数据")
    return list(deduplicated.values())


def write_v2_realtime_metrics_in_session(
    session: Session,
    *,
    batch_no: str,
    normalized_rows_by_indicator: dict[str, list[dict[str, Any]]],
    stat_date: date,
    collected_at: datetime,
    chunk_size: int | None = None,
) -> dict[str, Any]:
    """Write all realtime indicators without committing the caller transaction."""
    run = _load_writable_run(session, batch_no, expected_run_type="REALTIME")
    indicators = _load_store_indicators(session, normalized_rows_by_indicator)
    current_values = _build_current_values(
        run_id=run.id,
        indicators=indicators,
        rows_by_indicator=normalized_rows_by_indicator,
        stat_date=stat_date,
        collected_at=collected_at,
    )
    node_types = _load_metric_node_types(
        session, {value["node_id"] for value in current_values}
    )
    existing = _lock_current_values(
        session,
        node_ids=set(node_types),
        indicator_ids={value["indicator_id"] for value in current_values},
    )
    snapshot_values, sparse_stats = build_v2_sparse_snapshot_plan(
        run_id=run.id,
        current_values=current_values,
        node_types=node_types,
        existing_by_key=existing,
        collected_at=collected_at,
    )

    resolved_chunk_size = chunk_size or mysql_write_chunk_size()
    snapshot_started = perf_counter()
    snapshot_stats = execute_mysql_chunks(
        session,
        snapshot_values,
        operation="v2_snapshot_insert",
        chunk_size=resolved_chunk_size,
        statement_factory=lambda chunk: mysql_insert(MetricSnapshotV2).values(chunk),
    )
    snapshot_seconds = perf_counter() - snapshot_started
    snapshot_stats["rows_per_second"] = rows_per_second(
        len(snapshot_values), snapshot_seconds
    )

    current_started = perf_counter()
    current_stats = execute_mysql_chunks(
        session,
        current_values,
        operation="v2_current_upsert",
        chunk_size=resolved_chunk_size,
        statement_factory=build_v2_current_upsert_statement,
    )
    current_seconds = perf_counter() - current_started
    current_stats["rows_per_second"] = rows_per_second(
        len(current_values), current_seconds
    )
    return {
        "batch_no": batch_no,
        "current_upsert_count": len(current_values),
        "snapshot_insert_count": len(snapshot_values),
        "write_stats": {
            "chunk_size": resolved_chunk_size,
            "sparse": sparse_stats,
            "snapshot": snapshot_stats,
            "current": current_stats,
        },
    }


def write_v2_acc_metrics_in_session(
    session: Session,
    *,
    batch_no: str,
    normalized_rows_by_indicator: dict[str, list[dict[str, Any]]],
    period_type: str,
    stat_date: date,
    collected_at: datetime,
    chunk_size: int | None = None,
) -> dict[str, Any]:
    """Upsert DAY_ACC or MONTH values with out-of-order protection."""
    normalized_period = str(period_type or "").strip().upper()
    if normalized_period not in {"DAY_ACC", "MONTH"}:
        raise ValueError("period_type 只支持 DAY_ACC/MONTH")
    run = _load_writable_run(session, batch_no, expected_run_type=normalized_period)
    indicators = _load_store_indicators(session, normalized_rows_by_indicator)
    values = _build_acc_values(
        run_id=run.id,
        indicators=indicators,
        rows_by_indicator=normalized_rows_by_indicator,
        period_type=normalized_period,
        stat_date=stat_date,
        collected_at=collected_at,
    )
    _load_metric_node_types(session, {value["node_id"] for value in values})
    resolved_chunk_size = chunk_size or mysql_write_chunk_size()
    stats = execute_mysql_chunks(
        session,
        values,
        operation="v2_acc_upsert",
        chunk_size=resolved_chunk_size,
        statement_factory=build_v2_acc_upsert_statement,
    )
    return {
        "batch_no": batch_no,
        "period_type": normalized_period,
        "acc_upsert_count": len(values),
        "write_stats": {"chunk_size": resolved_chunk_size, "acc": stats},
    }


def finalize_v2_run_in_session(
    session: Session,
    *,
    batch_no: str,
    stat_date: date,
    finished_at: datetime,
    current_upsert_count: int = 0,
    snapshot_insert_count: int = 0,
    acc_upsert_count: int = 0,
) -> None:
    """Mark the run successful in the same transaction as metric writes."""
    run = session.scalar(
        select(CollectionRunV2)
        .where(CollectionRunV2.batch_no == batch_no)
        .with_for_update()
    )
    if run is None:
        raise FileNotFoundError(f"驾驶舱 V2 批次不存在: {batch_no}")
    if run.status != "RUNNING":
        raise MetricWriteError(f"V2 批次不可完成: status={run.status}")
    run.status = "SUCCESS"
    run.phase = "COMPLETED"
    run.stat_date = stat_date
    run.finished_at = finished_at
    run.current_upsert_count = current_upsert_count
    run.snapshot_insert_count = snapshot_insert_count
    run.acc_upsert_count = acc_upsert_count
    run.error_type = None
    run.error_message = None


def build_v2_sparse_snapshot_plan(
    *,
    run_id: int,
    current_values: list[dict[str, Any]],
    node_types: dict[int, str],
    existing_by_key: dict[tuple[int, int], Any],
    collected_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep only CHANNEL sparse; managers and all upper levels remain full."""
    stats: dict[str, Any] = {
        "input_row_count": len(current_values),
        "snapshot_inserted_row_count": 0,
        "snapshot_skipped_row_count": 0,
        "reduction_percent": 0.0,
        "channel_new_row_count": 0,
        "channel_changed_row_count": 0,
        "channel_daily_checkpoint_row_count": 0,
        "channel_unchanged_skipped_row_count": 0,
        "non_channel_full_row_count": 0,
        "manager_full_row_count": 0,
        "unknown_node_type_row_count": 0,
    }
    snapshots: list[dict[str, Any]] = []
    for value in current_values:
        key = (value["node_id"], value["indicator_id"])
        previous = existing_by_key.get(key)
        node_type = node_types.get(value["node_id"])
        if node_type == "CHANNEL":
            if previous is None:
                stats["channel_new_row_count"] += 1
            elif previous.stat_date != value["stat_date"]:
                stats["channel_daily_checkpoint_row_count"] += 1
            elif previous.metric_value == value["metric_value"]:
                stats["channel_unchanged_skipped_row_count"] += 1
                continue
            else:
                stats["channel_changed_row_count"] += 1
        else:
            stats["non_channel_full_row_count"] += 1
            if node_type == "CHANNEL_MANAGER":
                stats["manager_full_row_count"] += 1
            if node_type is None:
                stats["unknown_node_type_row_count"] += 1
        snapshots.append(
            {
                "collection_run_id": run_id,
                "node_id": value["node_id"],
                "indicator_id": value["indicator_id"],
                "metric_value": value["metric_value"],
                "collected_at": collected_at,
            }
        )
    inserted = len(snapshots)
    skipped = len(current_values) - inserted
    stats["snapshot_inserted_row_count"] = inserted
    stats["snapshot_skipped_row_count"] = skipped
    stats["reduction_percent"] = (
        round(skipped * 100 / len(current_values), 2) if current_values else 0.0
    )
    return snapshots, stats


def build_v2_current_upsert_statement(values: list[dict[str, Any]]):
    statement = mysql_insert(MetricCurrentV2).values(values)
    is_not_older = statement.inserted.collected_at >= MetricCurrentV2.collected_at
    return statement.on_duplicate_key_update(
        collection_run_id=case(
            (is_not_older, statement.inserted.collection_run_id),
            else_=MetricCurrentV2.collection_run_id,
        ),
        metric_value=case(
            (is_not_older, statement.inserted.metric_value),
            else_=MetricCurrentV2.metric_value,
        ),
        stat_date=case(
            (is_not_older, statement.inserted.stat_date),
            else_=MetricCurrentV2.stat_date,
        ),
        collected_at=case(
            (is_not_older, statement.inserted.collected_at),
            else_=MetricCurrentV2.collected_at,
        ),
        updated_at=case(
            (is_not_older, statement.inserted.updated_at),
            else_=MetricCurrentV2.updated_at,
        ),
    )


def build_v2_acc_upsert_statement(values: list[dict[str, Any]]):
    statement = mysql_insert(MetricAccV2).values(values)
    is_not_older = statement.inserted.collected_at >= MetricAccV2.collected_at
    return statement.on_duplicate_key_update(
        collection_run_id=case(
            (is_not_older, statement.inserted.collection_run_id),
            else_=MetricAccV2.collection_run_id,
        ),
        metric_value=case(
            (is_not_older, statement.inserted.metric_value),
            else_=MetricAccV2.metric_value,
        ),
        collected_at=case(
            (is_not_older, statement.inserted.collected_at),
            else_=MetricAccV2.collected_at,
        ),
        updated_at=case(
            (is_not_older, statement.inserted.updated_at),
            else_=MetricAccV2.updated_at,
        ),
    )


def _load_writable_run(
    session: Session,
    batch_no: str,
    *,
    expected_run_type: str,
) -> CollectionRunV2:
    run = session.scalar(
        select(CollectionRunV2)
        .where(CollectionRunV2.batch_no == batch_no)
        .with_for_update()
    )
    if run is None:
        raise FileNotFoundError(f"驾驶舱 V2 批次不存在: {batch_no}")
    if (
        run.run_type != expected_run_type
        or run.status != "RUNNING"
        or run.phase != "AREA_VALIDATED"
    ):
        raise MetricWriteError(
            f"V2 批次尚未完成节点校验: run_type={run.run_type}, "
            f"status={run.status}, phase={run.phase}"
        )
    return run


def _load_store_indicators(
    session: Session,
    rows_by_indicator: dict[str, list[dict[str, Any]]],
) -> dict[str, IndicatorV2]:
    codes = list(rows_by_indicator)
    indicators = list(
        session.scalars(
            select(IndicatorV2).where(
                IndicatorV2.code.in_(codes),
                IndicatorV2.enabled.is_(True),
                IndicatorV2.storage_mode == "STORE",
            )
        )
    )
    by_code = {indicator.code: indicator for indicator in indicators}
    missing = [code for code in codes if code not in by_code]
    if missing:
        raise MetricWriteError(f"V2 启用指标不存在: {', '.join(missing)}")
    return by_code


def _load_metric_node_types(
    session: Session,
    node_ids: set[int],
) -> dict[int, str]:
    rows = session.execute(
        select(HierarchyNode.id, HierarchyNode.node_type).where(
            HierarchyNode.id.in_(node_ids),
            HierarchyNode.enabled.is_(True),
            HierarchyNode.metric_enabled.is_(True),
        )
    ).all()
    result = {row.id: row.node_type for row in rows}
    missing = sorted(node_ids - set(result))
    if missing:
        raise MetricWriteError(f"V2 指标节点不存在或未启用: {missing[:10]}")
    return result


def _lock_current_values(
    session: Session,
    *,
    node_ids: set[int],
    indicator_ids: set[int],
) -> dict[tuple[int, int], MetricCurrentV2]:
    rows = session.scalars(
        select(MetricCurrentV2)
        .where(
            MetricCurrentV2.node_id.in_(node_ids),
            MetricCurrentV2.indicator_id.in_(indicator_ids),
        )
        .order_by(MetricCurrentV2.node_id, MetricCurrentV2.indicator_id)
        .with_for_update()
    ).all()
    return {(row.node_id, row.indicator_id): row for row in rows}


def _build_current_values(
    *,
    run_id: int,
    indicators: dict[str, IndicatorV2],
    rows_by_indicator: dict[str, list[dict[str, Any]]],
    stat_date: date,
    collected_at: datetime,
) -> list[dict[str, Any]]:
    values = [
        {
            "node_id": row["node_id"],
            "indicator_id": indicators[code].id,
            "collection_run_id": run_id,
            "metric_value": row["metric_value"],
            "stat_date": stat_date,
            "collected_at": collected_at,
            "updated_at": collected_at,
        }
        for code, rows in rows_by_indicator.items()
        for row in rows
    ]
    return sorted(values, key=lambda item: (item["node_id"], item["indicator_id"]))


def _build_acc_values(
    *,
    run_id: int,
    indicators: dict[str, IndicatorV2],
    rows_by_indicator: dict[str, list[dict[str, Any]]],
    period_type: str,
    stat_date: date,
    collected_at: datetime,
) -> list[dict[str, Any]]:
    values = [
        {
            "period_type": period_type,
            "stat_date": stat_date,
            "node_id": row["node_id"],
            "indicator_id": indicators[code].id,
            "collection_run_id": run_id,
            "metric_value": row["metric_value"],
            "collected_at": collected_at,
            "updated_at": collected_at,
        }
        for code, rows in rows_by_indicator.items()
        for row in rows
    ]
    return sorted(values, key=lambda item: (item["node_id"], item["indicator_id"]))


def mysql_write_chunk_size() -> int:
    raw = os.environ.get(
        "DASHBOARD_MYSQL_WRITE_CHUNK_SIZE",
        str(DEFAULT_MYSQL_WRITE_CHUNK_SIZE),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MYSQL_WRITE_CHUNK_SIZE
    return value if 1 <= value <= MAX_MYSQL_WRITE_CHUNK_SIZE else DEFAULT_MYSQL_WRITE_CHUNK_SIZE


def execute_mysql_chunks(
    session: Session,
    values: list[dict[str, Any]],
    *,
    operation: str,
    chunk_size: int,
    statement_factory: Callable[[list[dict[str, Any]]], Any],
) -> dict[str, Any]:
    chunk_count = 0
    slowest = 0.0
    for start in range(0, len(values), chunk_size):
        chunk_count += 1
        chunk = values[start : start + chunk_size]
        started = perf_counter()
        session.execute(statement_factory(chunk))
        elapsed = perf_counter() - started
        slowest = max(slowest, elapsed)
        if elapsed >= SLOW_MYSQL_WRITE_CHUNK_SECONDS:
            logger.warning(
                "V2 MySQL 写入分块耗时过长 operation=%s chunk=%s rows=%s seconds=%.3f",
                operation,
                chunk_count,
                len(chunk),
                elapsed,
            )
    return {
        "row_count": len(values),
        "chunk_count": chunk_count,
        "slowest_chunk_seconds": round(slowest, 3),
    }


def rows_per_second(row_count: int, elapsed_seconds: float) -> float:
    if row_count <= 0 or elapsed_seconds <= 0:
        return 0.0
    return round(row_count / elapsed_seconds, 1)
