"""partition metric_snapshot by collected day

Revision ID: 20260627_0023
Revises: 20260626_0022
Create Date: 2026-06-27
"""

from datetime import date
from typing import Sequence, Union

from alembic import op


revision: str = "20260627_0023"
down_revision: Union[str, Sequence[str], None] = "20260626_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _partition_sql() -> str:
    clauses = [
        "PARTITION p_history VALUES LESS THAN ('2026-01-01')",
    ]
    current = date(2026, 1, 1)
    end = date(2026, 8, 1)
    while current < end:
        boundary = current.fromordinal(current.toordinal() + 1)
        clauses.append(
            f"PARTITION p{current:%Y%m%d} VALUES LESS THAN ('{boundary:%Y-%m-%d}')"
        )
        current = boundary
    clauses.append("PARTITION p_future VALUES LESS THAN (MAXVALUE)")
    return ",\n            ".join(clauses)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return

    # MySQL partitioned InnoDB tables cannot retain foreign keys. The service
    # validates referenced IDs before writes and retention checks all remaining
    # snapshot references before deleting collection runs.
    op.execute(
        f"""
        ALTER TABLE metric_snapshot
            DROP FOREIGN KEY fk_metric_snapshot_area_id_area,
            DROP FOREIGN KEY fk_metric_snapshot_collection_run_id_collection_run,
            DROP FOREIGN KEY fk_metric_snapshot_indicator_id_indicator,
            DROP INDEX uq_metric_snapshot_run_area_indicator,
            DROP PRIMARY KEY,
            ADD PRIMARY KEY (id, collected_at),
            ADD UNIQUE KEY uq_metric_snapshot_run_area_indicator
                (collection_run_id, area_id, indicator_id, collected_at)
        PARTITION BY RANGE COLUMNS (collected_at) (
            {_partition_sql()}
        )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return

    op.execute(
        """
        ALTER TABLE metric_snapshot
            REMOVE PARTITIONING,
            DROP INDEX uq_metric_snapshot_run_area_indicator,
            DROP PRIMARY KEY,
            ADD PRIMARY KEY (id),
            ADD UNIQUE KEY uq_metric_snapshot_run_area_indicator
                (collection_run_id, area_id, indicator_id)
        """
    )
    op.create_foreign_key(
        "fk_metric_snapshot_area_id_area",
        "metric_snapshot",
        "area",
        ["area_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_metric_snapshot_collection_run_id_collection_run",
        "metric_snapshot",
        "collection_run",
        ["collection_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_metric_snapshot_indicator_id_indicator",
        "metric_snapshot",
        "indicator",
        ["indicator_id"],
        ["id"],
        ondelete="RESTRICT",
    )
