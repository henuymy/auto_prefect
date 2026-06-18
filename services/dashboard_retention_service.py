"""Data retention service — clean up expired snapshots and collection runs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from models.dashboard_collection_run import CollectionRun
from models.dashboard_metric import MetricAcc, MetricSnapshot

logger = logging.getLogger(__name__)

# Default retention periods (days).
_DEFAULT_SNAPSHOT_DAYS = 7
_DEFAULT_RUN_DAYS = 7
_DEFAULT_ACC_DAYS = 90


def cleanup_expired_data(
    session: Session,
    *,
    snapshot_retention_days: int = _DEFAULT_SNAPSHOT_DAYS,
    run_retention_days: int = _DEFAULT_RUN_DAYS,
    acc_retention_days: int = _DEFAULT_ACC_DAYS,
) -> dict[str, int]:
    """Delete expired snapshots, collection runs and old accumulated metrics.

    Deletion order respects FK dependencies:
    1. ``metric_snapshot`` (references ``collection_run.id``)
    2. ``metric_acc`` (references ``collection_run.id``)
    3. ``collection_run`` (referenced by both tables above)

    Only ``SUCCESS`` / ``FAILED`` runs are eligible for deletion —
    ``PENDING`` and ``RUNNING`` runs are always preserved.

    Returns counts of deleted rows per table.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    snapshot_cutoff = now - timedelta(days=snapshot_retention_days)
    run_cutoff = now - timedelta(days=run_retention_days)
    acc_cutoff_date = (now - timedelta(days=acc_retention_days)).date()

    # Step 1 — delete expired snapshots
    snapshot_result = session.execute(
        delete(MetricSnapshot).where(
            MetricSnapshot.collected_at < snapshot_cutoff,
        )
    )
    snapshot_deleted = snapshot_result.rowcount

    # Step 2 — delete expired accumulated metrics (older than retention)
    acc_result = session.execute(
        delete(MetricAcc).where(
            MetricAcc.stat_date < acc_cutoff_date,
        )
    )
    acc_deleted = acc_result.rowcount

    # Step 3 — collect IDs of finished runs older than cutoff
    # Only delete a collection_run when all referencing snapshot/acc rows
    # have already been removed above.
    run_ids_to_delete = session.execute(
        select(CollectionRun.id).where(
            CollectionRun.created_at < run_cutoff,
            CollectionRun.status.in_(["SUCCESS", "FAILED"]),
        )
    ).scalars().all()

    run_deleted = 0
    if run_ids_to_delete:
        # Verify no remaining snapshot references (safety check)
        orphan_count = session.scalar(
            select(func.count())
            .select_from(MetricSnapshot)
            .where(MetricSnapshot.collection_run_id.in_(run_ids_to_delete))
        )
        if orphan_count == 0:
            run_result = session.execute(
                delete(CollectionRun).where(
                    CollectionRun.id.in_(run_ids_to_delete),
                )
            )
            run_deleted = run_result.rowcount

    session.commit()

    if snapshot_deleted or run_deleted or acc_deleted:
        logger.info(
            "数据清理完成: 快照删除 %d 行, 累计删除 %d 行, 采集记录删除 %d 行",
            snapshot_deleted,
            acc_deleted,
            run_deleted,
        )

    return {
        "snapshot_deleted": snapshot_deleted,
        "acc_deleted": acc_deleted,
        "run_deleted": run_deleted,
    }
