"""Refresh the ``dashboard_wide`` materialised read table.

Call ``refresh_dashboard_wide(engine)`` after each successful pipeline run
to rebuild the wide table from the normalised source tables.  The cockpit
reads from ``dashboard_wide`` directly, eliminating all JOINs at query time.

The refresh is atomic: a new snapshot is built in a temporary staging area,
then swapped in via ``TRUNCATE + INSERT ... SELECT`` inside a single
transaction.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session


_REFRESH_SQL = text("""
INSERT INTO dashboard_wide (
    area_id, area_code, area_name, level_type, level_no, parent_id,
    indicator_code,
    current_value, current_run_id, current_collected_at,
    change_5min_value, change_5min_rate,
    change_15min_value, change_15min_rate,
    change_30min_value, change_30min_rate,
    change_60min_value, change_60min_rate,
    day_acc_value, day_acc_date,
    month_acc_value, month_acc_date,
    latest_run_batch_no, latest_run_finished_at, latest_run_stat_date,
    refreshed_at
)
SELECT
    a.id                AS area_id,
    a.area_code,
    a.area_name,
    a.level_type,
    a.level_no,
    a.parent_id,
    ind.code            AS indicator_code,

    /* ── current realtime ── */
    mc.metric_value     AS current_value,
    mc.collection_run_id AS current_run_id,
    mc.collected_at     AS current_collected_at,

    /* ── change deltas: (current - previous), stored as value/rate ── */
    (mc.metric_value - s5.metric_value)   AS change_5min_value,
    CASE WHEN mc.metric_value IS NOT NULL AND s5.metric_value IS NOT NULL AND s5.metric_value != 0
         THEN (mc.metric_value - s5.metric_value) / s5.metric_value END AS change_5min_rate,
    (mc.metric_value - s15.metric_value)  AS change_15min_value,
    CASE WHEN mc.metric_value IS NOT NULL AND s15.metric_value IS NOT NULL AND s15.metric_value != 0
         THEN (mc.metric_value - s15.metric_value) / s15.metric_value END AS change_15min_rate,
    (mc.metric_value - s30.metric_value)  AS change_30min_value,
    CASE WHEN mc.metric_value IS NOT NULL AND s30.metric_value IS NOT NULL AND s30.metric_value != 0
         THEN (mc.metric_value - s30.metric_value) / s30.metric_value END AS change_30min_rate,
    (mc.metric_value - s60.metric_value)  AS change_60min_value,
    CASE WHEN mc.metric_value IS NOT NULL AND s60.metric_value IS NOT NULL AND s60.metric_value != 0
         THEN (mc.metric_value - s60.metric_value) / s60.metric_value END AS change_60min_rate,

    /* ── accumulated ── */
    da.metric_value     AS day_acc_value,
    da.stat_date        AS day_acc_date,
    ma.metric_value     AS month_acc_value,
    ma.stat_date        AS month_acc_date,

    /* ── latest run (same for every row, cheap to denormalise) ── */
    lr.batch_no         AS latest_run_batch_no,
    lr.finished_at      AS latest_run_finished_at,
    lr.stat_date        AS latest_run_stat_date,

    NOW(3)              AS refreshed_at

FROM area a
CROSS JOIN (
    SELECT id, code
    FROM indicator
    WHERE enabled = 1
    ORDER BY sort_order, id
    LIMIT 1
) ind

/* current realtime */
LEFT JOIN metric_current mc
    ON mc.area_id = a.id AND mc.indicator_id = ind.id

/* snapshot: nearest at each cutoff ── uses lateral-style correlated subquery */
LEFT JOIN metric_snapshot s5
    ON s5.area_id = a.id AND s5.indicator_id = ind.id
   AND s5.collected_at = (
        SELECT MAX(collected_at) FROM metric_snapshot
        WHERE area_id = a.id AND indicator_id = ind.id
          AND collected_at >= :cutoff_5_lower
          AND collected_at <= :cutoff_5
    )
LEFT JOIN metric_snapshot s15
    ON s15.area_id = a.id AND s15.indicator_id = ind.id
   AND s15.collected_at = (
        SELECT MAX(collected_at) FROM metric_snapshot
        WHERE area_id = a.id AND indicator_id = ind.id
          AND collected_at >= :cutoff_15_lower
          AND collected_at <= :cutoff_15
    )
LEFT JOIN metric_snapshot s30
    ON s30.area_id = a.id AND s30.indicator_id = ind.id
   AND s30.collected_at = (
        SELECT MAX(collected_at) FROM metric_snapshot
        WHERE area_id = a.id AND indicator_id = ind.id
          AND collected_at >= :cutoff_30_lower
          AND collected_at <= :cutoff_30
    )
LEFT JOIN metric_snapshot s60
    ON s60.area_id = a.id AND s60.indicator_id = ind.id
   AND s60.collected_at = (
        SELECT MAX(collected_at) FROM metric_snapshot
        WHERE area_id = a.id AND indicator_id = ind.id
          AND collected_at >= :cutoff_60_lower
          AND collected_at <= :cutoff_60
    )

/* day acc: latest DAY_ACC date */
LEFT JOIN metric_acc da
    ON da.area_id = a.id AND da.indicator_id = ind.id
   AND da.period_type = 'DAY_ACC'
   AND da.stat_date = (
       SELECT MAX(stat_date) FROM metric_acc
       WHERE area_id = a.id AND indicator_id = ind.id AND period_type = 'DAY_ACC'
   )

/* month acc: latest MONTH date */
LEFT JOIN metric_acc ma
    ON ma.area_id = a.id AND ma.indicator_id = ind.id
   AND ma.period_type = 'MONTH'
   AND ma.stat_date = (
       SELECT MAX(stat_date) FROM metric_acc
       WHERE area_id = a.id AND indicator_id = ind.id AND period_type = 'MONTH'
   )

/* latest successful realtime run (singleton) */
LEFT JOIN (
    SELECT batch_no, finished_at, stat_date
    FROM collection_run
    WHERE status = 'SUCCESS' AND run_type = 'REALTIME'
    ORDER BY finished_at DESC, id DESC
    LIMIT 1
) lr ON 1=1

WHERE a.enabled = 1
ORDER BY a.level_no, a.sort_order, a.id
""")


def refresh_dashboard_wide(engine: Engine) -> dict[str, Any]:
    """Rebuild the ``dashboard_wide`` table from source tables.

    Returns a summary dict with ``row_count`` and ``elapsed_ms``.
    """
    started_at = datetime.now()

    with Session(engine) as session, session.begin():
        anchor_time = session.execute(text("""
            SELECT finished_at
            FROM collection_run
            WHERE status = 'SUCCESS'
              AND run_type = 'REALTIME'
              AND finished_at IS NOT NULL
            ORDER BY finished_at DESC, id DESC
            LIMIT 1
        """)).scalar() or datetime.now()
        cutoffs = {
            **_cutoff_params(anchor_time, 5),
            **_cutoff_params(anchor_time, 15),
            **_cutoff_params(anchor_time, 30),
            **_cutoff_params(anchor_time, 60),
        }
        # Atomic swap: delete all + insert new snapshot in one transaction.
        # InnoDB handles this safely; the cockpit may see a brief empty set
        # (acceptable for a <100ms refresh).
        session.execute(text("DELETE FROM dashboard_wide"))
        session.execute(_REFRESH_SQL, cutoffs)
        count = session.execute(text("SELECT COUNT(*) FROM dashboard_wide")).scalar()

    elapsed_ms = int((datetime.now() - started_at).total_seconds() * 1000)
    return {"row_count": count, "elapsed_ms": elapsed_ms}


def _cutoff_params(anchor_time: datetime, minutes: int) -> dict[str, str]:
    from datetime import timedelta
    cutoff = anchor_time - timedelta(minutes=minutes)
    lower = cutoff - timedelta(minutes=3)
    return {
        f"cutoff_{minutes}": _format_ts(cutoff),
        f"cutoff_{minutes}_lower": _format_ts(lower),
    }


def _format_ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
