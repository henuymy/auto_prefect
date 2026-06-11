"""simplify metric time columns

Revision ID: 20260610_0006
Revises: 20260610_0005
Create Date: 2026-06-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260610_0006"
down_revision: Union[str, Sequence[str], None] = "20260610_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_metric_snapshot_stat_date_indicator",
        table_name="metric_snapshot",
    )
    op.drop_column("metric_snapshot", "stat_date")
    op.drop_column("metric_snapshot", "created_at")
    op.drop_column("metric_current", "created_at")


def downgrade() -> None:
    op.add_column(
        "metric_current",
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
    )
    op.add_column(
        "metric_snapshot",
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
    )
    op.add_column(
        "metric_snapshot",
        sa.Column("stat_date", sa.Date(), nullable=True),
    )
    op.execute(
        "UPDATE metric_snapshot SET stat_date = DATE(collected_at) "
        "WHERE stat_date IS NULL"
    )
    op.alter_column(
        "metric_snapshot",
        "stat_date",
        existing_type=sa.Date(),
        nullable=False,
    )
    op.create_index(
        "ix_metric_snapshot_stat_date_indicator",
        "metric_snapshot",
        ["stat_date", "indicator_id"],
        unique=False,
    )
