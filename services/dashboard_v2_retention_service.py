"""Partition maintenance and retention for the Dashboard V2 schema."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from models.dashboard_v2 import (
    CollectionRunV2,
    HierarchyParentHistory,
    MetricAccV2,
    MetricCurrentV2,
    MetricSnapshotV2,
)
from models.monitor import MonitorRun


logger = logging.getLogger(__name__)
_PARTITION_PATTERN = re.compile(r"^p(\d{8})$")
_MAX_RETENTION_DAYS = 3650
_MAX_TIMEOUT_MINUTES = 7 * 24 * 60


def _bounded_int(value: object, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} 必须在 {minimum}-{maximum} 之间")
    return parsed


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


def _partition_date(name: str) -> datetime | None:
    match = _PARTITION_PATTERN.fullmatch(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError:
        return None


def _partition_clause(day: datetime) -> str:
    boundary = day + timedelta(days=1)
    return (
        f"PARTITION p{day:%Y%m%d} "
        f"VALUES LESS THAN ('{boundary:%Y-%m-%d}')"
    )


def maintain_v2_snapshot_partitions(
    session: Session,
    *,
    now: datetime | None = None,
    snapshot_retention_days: int = 7,
    partition_ahead_days: int = 30,
) -> dict[str, int | bool]:
    """Drop expired daily partitions and create the configured future window."""
    retention = _bounded_int(
        snapshot_retention_days,
        "snapshot_retention_days",
        1,
        _MAX_RETENTION_DAYS,
    )
    ahead = _bounded_int(partition_ahead_days, "partition_ahead_days", 1, 366)
    if session.get_bind().dialect.name not in {"mysql", "mariadb"}:
        return {
            "partitioned": False,
            "partition_drop_count": 0,
            "partition_create_count": 0,
        }
    names = list(session.scalars(text("""
        SELECT PARTITION_NAME
        FROM information_schema.PARTITIONS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'metric_snapshot'
          AND PARTITION_NAME IS NOT NULL
    """)))
    if "p_future" not in names:
        raise RuntimeError("metric_snapshot 缺少 p_future 分区")
    resolved_now = now or _now_shanghai()
    cutoff = (resolved_now - timedelta(days=retention)).date()
    dated = sorted(
        (day, name)
        for name in names
        if (day := _partition_date(str(name))) is not None
    )
    drops = [name for day, name in dated if day.date() < cutoff]
    if "p_history" in names:
        drops.insert(0, "p_history")
    if drops:
        session.execute(text(
            "ALTER TABLE metric_snapshot DROP PARTITION " + ", ".join(drops)
        ))
    existing_days = {day.date() for day, name in dated if name not in drops}
    horizon = (resolved_now + timedelta(days=ahead)).date()
    cursor = cutoff
    new_days: list[datetime] = []
    while cursor <= horizon:
        if cursor not in existing_days:
            new_days.append(datetime.combine(cursor, datetime.min.time()))
        cursor += timedelta(days=1)
    if new_days:
        clauses = [_partition_clause(day) for day in new_days]
        split_expired_history = not dated and set(names) == {"p_future"}
        if split_expired_history:
            clauses.insert(
                0,
                f"PARTITION p_history VALUES LESS THAN ('{cutoff:%Y-%m-%d}')",
            )
        clauses.append("PARTITION p_future VALUES LESS THAN (MAXVALUE)")
        session.execute(text(
            "ALTER TABLE metric_snapshot REORGANIZE PARTITION p_future INTO ("
            + ", ".join(clauses)
            + ")"
        ))
        if split_expired_history:
            session.execute(text(
                "ALTER TABLE metric_snapshot DROP PARTITION p_history"
            ))
            drops.append("p_history")
    return {
        "partitioned": True,
        "partition_drop_count": len(drops),
        "partition_create_count": len(new_days),
    }


def recover_stale_v2_runs_for_maintenance(
    session: Session,
    *,
    now: datetime,
    active_run_timeout_minutes: int,
) -> int:
    timeout = _bounded_int(
        active_run_timeout_minutes,
        "active_run_timeout_minutes",
        1,
        _MAX_TIMEOUT_MINUTES,
    )
    result = session.execute(
        update(CollectionRunV2)
        .where(
            CollectionRunV2.status.in_(["PENDING", "RUNNING"]),
            func.coalesce(
                CollectionRunV2.updated_at,
                CollectionRunV2.started_at,
                CollectionRunV2.created_at,
            ) < now - timedelta(minutes=timeout),
        )
        .values(
            status="FAILED",
            phase="RECOVER_TIMEOUT",
            finished_at=now,
            error_type="STALE_RUN_TIMEOUT",
            error_message={
                "message": "采集进程未正常收口，维护任务已标记为超时失败",
                "timeout_minutes": timeout,
            },
        )
    )
    return int(result.rowcount or 0)


def cleanup_v2_expired_data(
    session: Session,
    *,
    now: datetime | None = None,
    snapshot_retention_days: int = 7,
    run_retention_days: int = 30,
    monitor_run_retention_days: int = 30,
    acc_retention_days: int = 90,
    active_run_timeout_minutes: int = 120,
    partition_ahead_days: int = 30,
) -> dict[str, int | bool]:
    """Run partition maintenance and delete non-partitioned retained data."""
    snapshot_days = _bounded_int(
        snapshot_retention_days, "snapshot_retention_days", 1, _MAX_RETENTION_DAYS
    )
    run_days = _bounded_int(
        run_retention_days, "run_retention_days", 1, _MAX_RETENTION_DAYS
    )
    monitor_run_days = _bounded_int(
        monitor_run_retention_days,
        "monitor_run_retention_days",
        1,
        _MAX_RETENTION_DAYS,
    )
    acc_days = _bounded_int(
        acc_retention_days, "acc_retention_days", 1, _MAX_RETENTION_DAYS
    )
    if run_days < snapshot_days:
        raise ValueError("run_retention_days 不能小于 snapshot_retention_days")
    resolved_now = now or _now_shanghai()
    snapshot_cutoff = (resolved_now - timedelta(days=snapshot_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    run_cutoff = resolved_now - timedelta(days=run_days)
    monitor_run_cutoff = resolved_now - timedelta(days=monitor_run_days)
    acc_cutoff = (resolved_now - timedelta(days=acc_days)).date()
    partition = maintain_v2_snapshot_partitions(
        session,
        now=resolved_now,
        snapshot_retention_days=snapshot_days,
        partition_ahead_days=partition_ahead_days,
    )
    if partition["partitioned"]:
        snapshot_deleted = 0
    else:
        snapshot_deleted = int(
            session.execute(
                delete(MetricSnapshotV2).where(
                    MetricSnapshotV2.collected_at < snapshot_cutoff
                )
            ).rowcount or 0
        )
    acc_deleted = int(
        session.execute(
            delete(MetricAccV2).where(MetricAccV2.stat_date < acc_cutoff)
        ).rowcount or 0
    )
    stale_recovered = recover_stale_v2_runs_for_maintenance(
        session,
        now=resolved_now,
        active_run_timeout_minutes=active_run_timeout_minutes,
    )
    eligible = select(CollectionRunV2.id).where(
        CollectionRunV2.created_at < run_cutoff,
        CollectionRunV2.status.in_(["SUCCESS", "FAILED"]),
        ~select(MetricSnapshotV2.id).where(
            MetricSnapshotV2.collection_run_id == CollectionRunV2.id
        ).exists(),
    )
    eligible_ids = list(session.scalars(eligible))
    references_cleared = 0
    if eligible_ids:
        for model in (
            MetricCurrentV2,
            MetricAccV2,
            HierarchyParentHistory,
        ):
            result = session.execute(
                update(model)
                .where(model.collection_run_id.in_(eligible_ids))
                .values(collection_run_id=None)
            )
            references_cleared += int(result.rowcount or 0)
        run_deleted = int(
            session.execute(
                delete(CollectionRunV2).where(CollectionRunV2.id.in_(eligible_ids))
            ).rowcount or 0
        )
    else:
        run_deleted = 0
    monitor_run_deleted = int(
        session.execute(
            delete(MonitorRun).where(
                MonitorRun.finished_at < monitor_run_cutoff,
                MonitorRun.status.in_(["succeeded", "failed", "cancelled"]),
            )
        ).rowcount
        or 0
    )
    session.commit()
    result = {
        **partition,
        "snapshot_deleted": snapshot_deleted,
        "acc_deleted": acc_deleted,
        "stale_run_recovered": stale_recovered,
        "run_reference_cleared": references_cleared,
        "run_deleted": run_deleted,
        "monitor_run_deleted": monitor_run_deleted,
    }
    logger.info("驾驶舱 V2 分区与保留维护完成 result=%s", result)
    return result


def execute_v2_retention_maintenance(
    engine: Engine,
    *,
    retention: dict[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, int | bool]:
    config = retention or {}
    with Session(engine) as session:
        return cleanup_v2_expired_data(
            session,
            now=now,
            snapshot_retention_days=config.get("snapshot_retention_days", 7),
            run_retention_days=config.get("run_retention_days", 30),
            monitor_run_retention_days=config.get("monitor_run_retention_days", 30),
            acc_retention_days=config.get("acc_retention_days", 90),
            active_run_timeout_minutes=config.get(
                "active_run_timeout_minutes", 120
            ),
            partition_ahead_days=config.get("partition_ahead_days", 30),
        )
