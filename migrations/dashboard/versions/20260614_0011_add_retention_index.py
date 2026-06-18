"""add retention helper indexes

Revision ID: 20260614_0011
Revises: 20260611_0010
Create Date: 2026-06-14
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260614_0011"
down_revision: Union[str, Sequence[str], None] = "20260611_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Speed up retention DELETE WHERE collected_at < cutoff
    op.create_index(
        "ix_metric_snapshot_collected_at",
        "metric_snapshot",
        ["collected_at"],
    )
    # Speed up retention DELETE WHERE status IN (...) AND created_at < cutoff
    op.create_index(
        "ix_collection_run_status_created_at",
        "collection_run",
        ["status", "created_at"],
    )
    # Speed up acc retention DELETE WHERE stat_date < cutoff
    op.create_index(
        "ix_metric_acc_stat_date",
        "metric_acc",
        ["stat_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_metric_acc_stat_date", table_name="metric_acc")
    op.drop_index("ix_collection_run_status_created_at", table_name="collection_run")
    op.drop_index("ix_metric_snapshot_collected_at", table_name="metric_snapshot")
