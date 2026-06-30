"""Atomic persistence for validated dashboard metric rows."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from time import perf_counter
from typing import Any, Callable, Iterable

from sqlalchemy import Engine, case, insert, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session, sessionmaker

from models.dashboard_area import Area  # noqa: F401
from models.dashboard_collection_run import CollectionRun
from models.dashboard_indicator import Indicator
from models.dashboard_metric import MetricAcc, MetricCurrent, MetricSnapshot


_DEFAULT_MYSQL_WRITE_CHUNK_SIZE = 2_000
_MAX_MYSQL_WRITE_CHUNK_SIZE = 10_000
_SLOW_MYSQL_WRITE_CHUNK_SECONDS = 2.0

logger = logging.getLogger(__name__)


class MetricWriteError(RuntimeError):
    phase = "WRITE_METRICS"


class MetricValueError(MetricWriteError):
    error_type = "INVALID_METRIC_VALUE"


class MetricConflictError(MetricWriteError):
    error_type = "DUPLICATE_METRIC_CONFLICT"


def parse_metric_value(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise MetricValueError(f"指标值不是有效数字: {value!r}")
    text = str(value).strip().replace(",", "")
    if not text:
        raise MetricValueError("指标值不能为空")
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise MetricValueError(f"指标值不是有效数字: {value!r}") from exc
    if not parsed.is_finite():
        raise MetricValueError(f"指标值必须是有限数字: {value!r}")
    significant = parsed.normalize() if parsed else parsed
    if significant.as_tuple().exponent < -4:
        raise MetricValueError(f"指标值最多保留4位小数: {value!r}")
    if abs(parsed) >= Decimal("10000000000000000"):
        raise MetricValueError(f"指标值超出 DECIMAL(20,4) 范围: {value!r}")
    return parsed


def normalize_metric_rows(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduplicated: dict[int, dict[str, Any]] = {}
    for source_row in rows:
        row = dict(source_row)
        area_id = row.get("area_id")
        if not isinstance(area_id, int) or area_id <= 0:
            raise MetricWriteError("指标数据缺少有效 area_id，必须先执行区域校验")
        raw_value = (
            row["metric_value"] if "metric_value" in row else row.get("raw_value")
        )
        metric_value = parse_metric_value(raw_value)
        existing = deduplicated.get(area_id)
        if existing is not None:
            if existing["metric_value"] != metric_value:
                raise MetricConflictError(
                    f"同一区域同一指标出现冲突值: area_id={area_id}, "
                    f"{existing['metric_value']} != {metric_value}"
                )
            continue
        deduplicated[area_id] = {
            "area_id": area_id,
            "metric_value": metric_value,
        }
    if not deduplicated:
        raise MetricWriteError("没有可写入的有效指标数据")
    return list(deduplicated.values())


def _load_writable_run(
    session: Session,
    batch_no: str,
    expected_run_type: str,
    scope: str,
) -> CollectionRun:
    run = session.scalar(
        select(CollectionRun)
        .where(CollectionRun.batch_no == batch_no)
        .with_for_update()
    )
    if run is None:
        raise FileNotFoundError(f"驾驶舱批次不存在: {batch_no}")
    if (
        run.run_type != expected_run_type
        or run.status != "RUNNING"
        or run.phase != "AREA_VALIDATED"
    ):
        raise MetricWriteError(
            f"{scope}批次尚未完成区域校验: "
            f"run_type={run.run_type}, status={run.status}, phase={run.phase}"
        )
    return run


def _load_enabled_indicator(
    session: Session,
    indicator_code: str,
) -> Indicator:
    indicator = session.scalar(
        select(Indicator).where(
            Indicator.code == indicator_code,
            Indicator.enabled.is_(True),
            Indicator.storage_mode == "STORE",
        )
    )
    if indicator is None:
        raise MetricWriteError(f"启用指标不存在: {indicator_code}")
    return indicator


def _complete_run(
    run: CollectionRun,
    stat_date: date,
    collected_at: datetime,
) -> None:
    run.status = "SUCCESS"
    run.phase = "COMPLETED"
    run.stat_date = stat_date
    run.finished_at = collected_at
    run.error_type = None
    run.error_message = None
    run.updated_at = datetime.now()


def write_metric_batch(
    engine: Engine,
    batch_no: str,
    indicator_code: str,
    rows: Iterable[dict[str, Any]],
    stat_date: date,
    collected_at: datetime,
) -> dict[str, Any]:
    normalized_rows = normalize_metric_rows(rows)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with session_factory.begin() as session:
        return write_metric_batch_in_session(
            session,
            dialect_name=engine.dialect.name,
            batch_no=batch_no,
            indicator_code=indicator_code,
            normalized_rows=normalized_rows,
            stat_date=stat_date,
            collected_at=collected_at,
        )


def write_metric_batch_in_session(
    session: Session,
    *,
    dialect_name: str,
    batch_no: str,
    indicator_code: str,
    normalized_rows: list[dict[str, Any]],
    stat_date: date,
    collected_at: datetime,
    finalize_run: bool = True,
) -> dict[str, Any]:
    run = _load_writable_run(
        session,
        batch_no,
        expected_run_type="REALTIME",
        scope="实时",
    )
    indicator = _load_enabled_indicator(session, indicator_code)

    if dialect_name == "mysql":
        snapshot_insert_count = _write_mysql_metrics(
            session,
            run.id,
            indicator.id,
            normalized_rows,
            stat_date,
            collected_at,
        )
    else:
        snapshot_insert_count = _write_orm_metrics(
            session,
            run.id,
            indicator.id,
            normalized_rows,
            stat_date,
            collected_at,
        )

    written_count = len(normalized_rows)
    if finalize_run:
        _complete_run(run, stat_date, collected_at)
        run.current_upsert_count = written_count
        run.snapshot_insert_count = snapshot_insert_count

    return {
        "batch_no": batch_no,
        "indicator_code": indicator_code,
        "current_upsert_count": written_count,
        "snapshot_insert_count": snapshot_insert_count,
        "status": "SUCCESS",
        "phase": "COMPLETED",
    }


def write_metric_batches_in_session(
    session: Session,
    *,
    dialect_name: str,
    batch_no: str,
    normalized_rows_by_indicator: dict[str, list[dict[str, Any]]],
    stat_date: date,
    collected_at: datetime,
) -> dict[str, Any]:
    """Write all realtime indicators through one metadata load and bulk path."""
    metadata_started = perf_counter()
    run = _load_writable_run(
        session,
        batch_no,
        expected_run_type="REALTIME",
        scope="实时",
    )
    indicator_codes = list(normalized_rows_by_indicator)
    indicators = session.scalars(
        select(Indicator).where(
            Indicator.code.in_(indicator_codes),
            Indicator.enabled.is_(True),
        )
    ).all()
    indicator_by_code = {indicator.code: indicator for indicator in indicators}
    missing_codes = [code for code in indicator_codes if code not in indicator_by_code]
    if missing_codes:
        raise MetricWriteError(f"指标不存在或未启用: {', '.join(missing_codes)}")
    metadata_load_seconds = perf_counter() - metadata_started

    current_values: list[dict[str, Any]] = []
    updated_at = datetime.now()
    for indicator_code, rows in normalized_rows_by_indicator.items():
        indicator_id = indicator_by_code[indicator_code].id
        for row in rows:
            current_values.append({
                "area_id": row["area_id"],
                "indicator_id": indicator_id,
                "collection_run_id": run.id,
                "metric_value": row["metric_value"],
                "stat_date": stat_date,
                "collected_at": collected_at,
                "updated_at": updated_at,
            })

    snapshot_values, sparse_stats = _sparse_snapshot_plan(
        session,
        run_id=run.id,
        current_values=current_values,
        collected_at=collected_at,
    )
    snapshot_counts_by_indicator: dict[int, int] = {}
    for value in snapshot_values:
        indicator_id = value["indicator_id"]
        snapshot_counts_by_indicator[indicator_id] = (
            snapshot_counts_by_indicator.get(indicator_id, 0) + 1
        )
    indicator_results = [
        {
            "batch_no": batch_no,
            "indicator_code": indicator_code,
            "current_upsert_count": len(rows),
            "snapshot_insert_count": snapshot_counts_by_indicator.get(
                indicator_by_code[indicator_code].id, 0
            ),
            "status": "SUCCESS",
            "phase": "COMPLETED",
        }
        for indicator_code, rows in normalized_rows_by_indicator.items()
    ]

    mysql_chunk_size = _mysql_write_chunk_size() if dialect_name == "mysql" else None
    snapshot_started = perf_counter()
    if dialect_name == "mysql":
        snapshot_stats = _insert_mysql_snapshots(
            session,
            snapshot_values,
            chunk_size=mysql_chunk_size,
        )
    elif snapshot_values:
        session.execute(insert(MetricSnapshot), snapshot_values)
        snapshot_stats = _single_write_stats(snapshot_values)
    else:
        snapshot_stats = _single_write_stats(snapshot_values)
    snapshot_insert_seconds = perf_counter() - snapshot_started
    snapshot_stats["rows_per_second"] = _rows_per_second(
        len(snapshot_values), snapshot_insert_seconds
    )

    current_started = perf_counter()
    if dialect_name == "mysql":
        current_stats = _upsert_mysql_current(
            session,
            current_values,
            chunk_size=mysql_chunk_size,
        )
    else:
        for indicator_code, rows in normalized_rows_by_indicator.items():
            _write_orm_current_metrics(
                session,
                run.id,
                indicator_by_code[indicator_code].id,
                rows,
                stat_date,
                collected_at,
            )
        current_stats = _single_write_stats(current_values)
    current_upsert_seconds = perf_counter() - current_started
    current_stats["rows_per_second"] = _rows_per_second(
        len(current_values), current_upsert_seconds
    )

    total_current = len(current_values)
    total_snapshots = len(snapshot_values)
    return {
        "batch_no": batch_no,
        "indicator_codes": indicator_codes,
        "indicator_results": indicator_results,
        "current_upsert_count": total_current,
        "snapshot_insert_count": total_snapshots,
        "timings": {
            "metadata_load_seconds": metadata_load_seconds,
            "snapshot_insert_seconds": snapshot_insert_seconds,
            "current_upsert_seconds": current_upsert_seconds,
        },
        "write_stats": {
            "chunk_size": mysql_chunk_size,
            "sparse": sparse_stats,
            "snapshot": snapshot_stats,
            "current": current_stats,
        },
    }


def write_acc_metric_batch(
    engine: Engine,
    batch_no: str,
    indicator_code: str,
    rows: Iterable[dict[str, Any]],
    stat_date: date,
    collected_at: datetime,
    period_type: str = "DAY_ACC",
) -> dict[str, Any]:
    normalized_period = str(period_type or "").strip().upper()
    if normalized_period not in {"DAY_ACC", "MONTH"}:
        raise ValueError(f"period_type 只支持 DAY_ACC/MONTH: {period_type!r}")
    expected_run_type = "DAILY" if normalized_period == "DAY_ACC" else "MONTHLY"
    normalized_rows = normalize_metric_rows(rows)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with session_factory.begin() as session:
        return write_acc_metric_batch_in_session(
            session,
            dialect_name=engine.dialect.name,
            batch_no=batch_no,
            indicator_code=indicator_code,
            normalized_rows=normalized_rows,
            stat_date=stat_date,
            collected_at=collected_at,
            period_type=normalized_period,
            expected_run_type=expected_run_type,
        )


def write_acc_metric_batch_in_session(
    session: Session,
    *,
    dialect_name: str,
    batch_no: str,
    indicator_code: str,
    normalized_rows: list[dict[str, Any]],
    stat_date: date,
    collected_at: datetime,
    period_type: str,
    expected_run_type: str,
    finalize_run: bool = True,
) -> dict[str, Any]:
    run = _load_writable_run(
        session,
        batch_no,
        expected_run_type=expected_run_type,
        scope="累计",
    )
    indicator = _load_enabled_indicator(session, indicator_code)

    values = [
        {
            "period_type": period_type,
            "stat_date": stat_date,
            "area_id": row["area_id"],
            "indicator_id": indicator.id,
            "collection_run_id": run.id,
            "metric_value": row["metric_value"],
            "collected_at": collected_at,
            "updated_at": collected_at,
        }
        for row in normalized_rows
    ]
    if dialect_name == "mysql":
        statement = mysql_insert(MetricAcc).values(values)
        statement = statement.on_duplicate_key_update(
            collection_run_id=statement.inserted.collection_run_id,
            metric_value=statement.inserted.metric_value,
            collected_at=statement.inserted.collected_at,
            updated_at=statement.inserted.updated_at,
        )
        session.execute(statement)
    else:
        _write_orm_daily_metrics(
            session,
            indicator.id,
            values,
            period_type,
        )

    written_count = len(normalized_rows)
    if finalize_run:
        _complete_run(run, stat_date, collected_at)
        run.acc_upsert_count = written_count

    return {
        "batch_no": batch_no,
        "indicator_code": indicator_code,
        "period_type": period_type,
        "stat_date": stat_date.isoformat(),
        "acc_upsert_count": written_count,
        "status": "SUCCESS",
        "phase": "COMPLETED",
    }


def finalize_metric_run_in_session(
    session: Session,
    *,
    batch_no: str,
    expected_run_type: str,
    stat_date: date,
    collected_at: datetime,
    current_upsert_count: int = 0,
    snapshot_insert_count: int = 0,
    acc_upsert_count: int = 0,
) -> None:
    run = _load_writable_run(
        session,
        batch_no,
        expected_run_type=expected_run_type,
        scope="指标",
    )
    _complete_run(run, stat_date, collected_at)
    run.current_upsert_count = current_upsert_count
    run.snapshot_insert_count = snapshot_insert_count
    run.acc_upsert_count = acc_upsert_count


def _write_mysql_metrics(
    session: Session,
    run_id: int,
    indicator_id: int,
    rows: list[dict[str, Any]],
    stat_date: date,
    collected_at: datetime,
) -> int:
    updated_at = datetime.now()
    current_values = [
        {
            "area_id": row["area_id"],
            "indicator_id": indicator_id,
            "collection_run_id": run_id,
            "metric_value": row["metric_value"],
            "stat_date": stat_date,
            "collected_at": collected_at,
            "updated_at": updated_at,
        }
        for row in rows
    ]
    snapshot_values = _sparse_snapshot_values(
        session,
        run_id=run_id,
        current_values=current_values,
        collected_at=collected_at,
    )
    chunk_size = _mysql_write_chunk_size()
    _insert_mysql_snapshots(session, snapshot_values, chunk_size=chunk_size)
    _upsert_mysql_current(session, current_values, chunk_size=chunk_size)
    return len(snapshot_values)


def _load_area_levels(
    session: Session,
    area_ids: set[int] | list[int],
) -> dict[int, str]:
    return {
        row.id: str(row.level_type or "").strip().upper()
        for row in session.execute(
            select(Area.id, Area.level_type).where(Area.id.in_(area_ids))
        )
    }


def _sparse_snapshot_values(
    session: Session,
    *,
    run_id: int,
    current_values: list[dict[str, Any]],
    collected_at: datetime,
) -> list[dict[str, Any]]:
    snapshots, _ = _sparse_snapshot_plan(
        session,
        run_id=run_id,
        current_values=current_values,
        collected_at=collected_at,
    )
    return snapshots


def _sparse_snapshot_plan(
    session: Session,
    *,
    run_id: int,
    current_values: list[dict[str, Any]],
    collected_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply sparse history only to channel-level values.

    CITY, BRANCH and GRID remain full snapshots on every run.  CHANNEL values
    emit on change and on their first observation of a new day.
    """
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
        "unknown_area_level_row_count": 0,
    }
    if not current_values:
        return [], stats
    area_ids = {value["area_id"] for value in current_values}
    indicator_ids = {value["indicator_id"] for value in current_values}
    area_levels = _load_area_levels(session, area_ids)
    existing = session.execute(
        select(
            MetricCurrent.area_id,
            MetricCurrent.indicator_id,
            MetricCurrent.metric_value,
            MetricCurrent.stat_date,
        ).where(
            MetricCurrent.area_id.in_(area_ids),
            MetricCurrent.indicator_id.in_(indicator_ids),
        ).with_for_update()
    ).all()
    existing_by_key = {
        (row.area_id, row.indicator_id): row
        for row in existing
    }
    snapshots: list[dict[str, Any]] = []
    for value in current_values:
        key = (value["area_id"], value["indicator_id"])
        previous = existing_by_key.get(key)
        area_level = area_levels.get(value["area_id"])
        if area_level == "CHANNEL":
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
            if area_level is None:
                stats["unknown_area_level_row_count"] += 1
        snapshots.append({
            "collection_run_id": run_id,
            "area_id": value["area_id"],
            "indicator_id": value["indicator_id"],
            "metric_value": value["metric_value"],
            "collected_at": collected_at,
        })
    inserted_count = len(snapshots)
    skipped_count = len(current_values) - inserted_count
    stats["snapshot_inserted_row_count"] = inserted_count
    stats["snapshot_skipped_row_count"] = skipped_count
    stats["reduction_percent"] = round(
        skipped_count * 100 / len(current_values), 2
    )
    return snapshots, stats


def _rows_per_second(row_count: int, elapsed_seconds: float) -> float:
    if row_count <= 0 or elapsed_seconds <= 0:
        return 0.0
    return round(row_count / elapsed_seconds, 1)


def _mysql_write_chunk_size() -> int:
    raw_value = os.environ.get(
        "DASHBOARD_MYSQL_WRITE_CHUNK_SIZE",
        str(_DEFAULT_MYSQL_WRITE_CHUNK_SIZE),
    )
    try:
        chunk_size = int(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            "DASHBOARD_MYSQL_WRITE_CHUNK_SIZE=%r 不是整数，使用默认值 %s",
            raw_value,
            _DEFAULT_MYSQL_WRITE_CHUNK_SIZE,
        )
        return _DEFAULT_MYSQL_WRITE_CHUNK_SIZE
    if not 1 <= chunk_size <= _MAX_MYSQL_WRITE_CHUNK_SIZE:
        logger.warning(
            "DASHBOARD_MYSQL_WRITE_CHUNK_SIZE=%r 超出范围 1-%s，使用默认值 %s",
            raw_value,
            _MAX_MYSQL_WRITE_CHUNK_SIZE,
            _DEFAULT_MYSQL_WRITE_CHUNK_SIZE,
        )
        return _DEFAULT_MYSQL_WRITE_CHUNK_SIZE
    return chunk_size


def _chunks(
    values: list[dict[str, Any]],
    chunk_size: int,
) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(values), chunk_size):
        yield values[start:start + chunk_size]


def _single_write_stats(values: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "row_count": len(values),
        "chunk_count": 1 if values else 0,
        "slowest_chunk_seconds": 0.0,
    }


def _insert_mysql_snapshots(
    session: Session,
    values: list[dict[str, Any]],
    *,
    chunk_size: int,
) -> dict[str, Any]:
    return _execute_mysql_chunks(
        session,
        values,
        operation="snapshot_insert",
        chunk_size=chunk_size,
        statement_factory=lambda chunk: mysql_insert(MetricSnapshot).values(chunk),
    )


def _upsert_mysql_current(
    session: Session,
    values: list[dict[str, Any]],
    *,
    chunk_size: int,
) -> dict[str, Any]:
    def statement_factory(chunk: list[dict[str, Any]]):
        statement = mysql_insert(MetricCurrent).values(chunk)
        is_not_older = statement.inserted.collected_at >= MetricCurrent.collected_at
        return statement.on_duplicate_key_update(
            collection_run_id=case(
                (is_not_older, statement.inserted.collection_run_id),
                else_=MetricCurrent.collection_run_id,
            ),
            metric_value=case(
                (is_not_older, statement.inserted.metric_value),
                else_=MetricCurrent.metric_value,
            ),
            stat_date=case(
                (is_not_older, statement.inserted.stat_date),
                else_=MetricCurrent.stat_date,
            ),
            updated_at=case(
                (is_not_older, statement.inserted.updated_at),
                else_=MetricCurrent.updated_at,
            ),
            collected_at=case(
                (is_not_older, statement.inserted.collected_at),
                else_=MetricCurrent.collected_at,
            ),
        )

    return _execute_mysql_chunks(
        session,
        values,
        operation="current_upsert",
        chunk_size=chunk_size,
        statement_factory=statement_factory,
    )


def _execute_mysql_chunks(
    session: Session,
    values: list[dict[str, Any]],
    *,
    operation: str,
    chunk_size: int,
    statement_factory: Callable[[list[dict[str, Any]]], Any],
) -> dict[str, Any]:
    chunk_count = 0
    slowest_chunk_seconds = 0.0
    for chunk_count, chunk in enumerate(_chunks(values, chunk_size), start=1):
        chunk_started = perf_counter()
        session.execute(statement_factory(chunk))
        chunk_seconds = perf_counter() - chunk_started
        slowest_chunk_seconds = max(slowest_chunk_seconds, chunk_seconds)
        if chunk_seconds >= _SLOW_MYSQL_WRITE_CHUNK_SECONDS:
            logger.warning(
                "驾驶舱 MySQL 写入分块耗时过长 operation=%s chunk=%s rows=%s seconds=%.3f",
                operation,
                chunk_count,
                len(chunk),
                chunk_seconds,
            )
    return {
        "row_count": len(values),
        "chunk_count": chunk_count,
        "slowest_chunk_seconds": round(slowest_chunk_seconds, 3),
    }


def _write_orm_metrics(
    session: Session,
    run_id: int,
    indicator_id: int,
    rows: list[dict[str, Any]],
    stat_date: date,
    collected_at: datetime,
) -> int:
    area_ids = [row["area_id"] for row in rows]
    current_by_area = {
        record.area_id: record
        for record in session.scalars(
            select(MetricCurrent).where(
                MetricCurrent.indicator_id == indicator_id,
                MetricCurrent.area_id.in_(area_ids),
            )
        ).all()
    }
    area_levels = _load_area_levels(session, area_ids)

    snapshot_count = 0
    for row in rows:
        current = current_by_area.get(row["area_id"])
        if (
            area_levels.get(row["area_id"]) != "CHANNEL"
            or current is None
            or current.metric_value != row["metric_value"]
            or current.stat_date != stat_date
        ):
            session.add(MetricSnapshot(
                collection_run_id=run_id,
                area_id=row["area_id"],
                indicator_id=indicator_id,
                metric_value=row["metric_value"],
                collected_at=collected_at,
            ))
            snapshot_count += 1
        if current is None:
            session.add(
                MetricCurrent(
                    area_id=row["area_id"],
                    indicator_id=indicator_id,
                    collection_run_id=run_id,
                    metric_value=row["metric_value"],
                    stat_date=stat_date,
                    collected_at=collected_at,
                )
            )
        elif collected_at >= current.collected_at:
            current.collection_run_id = run_id
            current.metric_value = row["metric_value"]
            current.stat_date = stat_date
            current.collected_at = collected_at
            current.updated_at = datetime.now()
    return snapshot_count


def _write_orm_current_metrics(
    session: Session,
    run_id: int,
    indicator_id: int,
    rows: list[dict[str, Any]],
    stat_date: date,
    collected_at: datetime,
) -> None:
    area_ids = [row["area_id"] for row in rows]
    current_by_area = {
        record.area_id: record
        for record in session.scalars(
            select(MetricCurrent).where(
                MetricCurrent.indicator_id == indicator_id,
                MetricCurrent.area_id.in_(area_ids),
            )
        ).all()
    }
    for row in rows:
        current = current_by_area.get(row["area_id"])
        if current is None:
            session.add(MetricCurrent(
                area_id=row["area_id"],
                indicator_id=indicator_id,
                collection_run_id=run_id,
                metric_value=row["metric_value"],
                stat_date=stat_date,
                collected_at=collected_at,
            ))
            continue
        if collected_at >= current.collected_at:
            current.collection_run_id = run_id
            current.metric_value = row["metric_value"]
            current.stat_date = stat_date
            current.collected_at = collected_at
            current.updated_at = datetime.now()


def _write_orm_daily_metrics(
    session: Session,
    indicator_id: int,
    values: list[dict[str, Any]],
    period_type: str,
) -> None:
    stat_date = values[0]["stat_date"]
    area_ids = [value["area_id"] for value in values]
    existing_by_area = {
        record.area_id: record
        for record in session.scalars(
            select(MetricAcc).where(
                MetricAcc.period_type == period_type,
                MetricAcc.stat_date == stat_date,
                MetricAcc.indicator_id == indicator_id,
                MetricAcc.area_id.in_(area_ids),
            )
        ).all()
    }
    for value in values:
        record = existing_by_area.get(value["area_id"])
        if record is None:
            session.add(MetricAcc(**value))
            continue
        record.collection_run_id = value["collection_run_id"]
        record.metric_value = value["metric_value"]
        record.collected_at = value["collected_at"]
        record.updated_at = value["updated_at"]
