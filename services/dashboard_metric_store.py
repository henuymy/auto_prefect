"""Atomic persistence for validated dashboard metric rows."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from sqlalchemy import Engine, insert, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session, sessionmaker

from models.dashboard_area import Area  # noqa: F401
from models.dashboard_collection_run import CollectionRun
from models.dashboard_indicator import Indicator
from models.dashboard_metric import MetricAcc, MetricCurrent, MetricSnapshot


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
        _write_mysql_metrics(
            session,
            run.id,
            indicator.id,
            normalized_rows,
            stat_date,
            collected_at,
        )
    else:
        _write_orm_metrics(
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
        run.snapshot_insert_count = written_count

    return {
        "batch_no": batch_no,
        "indicator_code": indicator_code,
        "current_upsert_count": written_count,
        "snapshot_insert_count": written_count,
        "status": "SUCCESS",
        "phase": "COMPLETED",
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
) -> None:
    snapshot_values = [
        {
            "collection_run_id": run_id,
            "area_id": row["area_id"],
            "indicator_id": indicator_id,
            "metric_value": row["metric_value"],
            "collected_at": collected_at,
        }
        for row in rows
    ]
    session.execute(insert(MetricSnapshot), snapshot_values)

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
    statement = mysql_insert(MetricCurrent).values(current_values)
    statement = statement.on_duplicate_key_update(
        collection_run_id=statement.inserted.collection_run_id,
        metric_value=statement.inserted.metric_value,
        stat_date=statement.inserted.stat_date,
        collected_at=statement.inserted.collected_at,
        updated_at=statement.inserted.updated_at,
    )
    session.execute(statement)


def _write_orm_metrics(
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
        session.add(
            MetricSnapshot(
                collection_run_id=run_id,
                area_id=row["area_id"],
                indicator_id=indicator_id,
                metric_value=row["metric_value"],
                collected_at=collected_at,
            )
        )
        current = current_by_area.get(row["area_id"])
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
        else:
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
