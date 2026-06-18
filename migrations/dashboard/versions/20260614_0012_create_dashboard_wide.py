"""create dashboard_wide materialised read table

Revision ID: 20260614_0012
Revises: 20260614_0011
Create Date: 2026-06-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260614_0012"
down_revision: Union[str, Sequence[str], None] = "20260614_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dashboard_wide",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True),

        # ── area identification ──
        sa.Column("area_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("area_code", sa.String(50), nullable=False),
        sa.Column("area_name", sa.String(200), nullable=False),
        sa.Column("level_type", sa.String(20), nullable=False),
        sa.Column("level_no", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("parent_id", mysql.BIGINT(unsigned=True), nullable=True),

        # ── indicator ──
        sa.Column("indicator_code", sa.String(100), nullable=False),

        # ── current realtime ──
        sa.Column("current_value", mysql.DECIMAL(20, 4), nullable=True),
        sa.Column("current_run_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("current_collected_at", mysql.DATETIME(fsp=3), nullable=True),

        # ── change deltas (pre-computed from snapshot) ──
        sa.Column("change_5min_value", mysql.DECIMAL(20, 4), nullable=True),
        sa.Column("change_5min_rate", mysql.DECIMAL(10, 6), nullable=True),
        sa.Column("change_15min_value", mysql.DECIMAL(20, 4), nullable=True),
        sa.Column("change_15min_rate", mysql.DECIMAL(10, 6), nullable=True),
        sa.Column("change_30min_value", mysql.DECIMAL(20, 4), nullable=True),
        sa.Column("change_30min_rate", mysql.DECIMAL(10, 6), nullable=True),
        sa.Column("change_60min_value", mysql.DECIMAL(20, 4), nullable=True),
        sa.Column("change_60min_rate", mysql.DECIMAL(10, 6), nullable=True),

        # ── accumulated ──
        sa.Column("day_acc_value", mysql.DECIMAL(20, 4), nullable=True),
        sa.Column("day_acc_date", sa.Date(), nullable=True),
        sa.Column("month_acc_value", mysql.DECIMAL(20, 4), nullable=True),
        sa.Column("month_acc_date", sa.Date(), nullable=True),

        # ── latest run metadata (denormalised for fast header display) ──
        sa.Column("latest_run_batch_no", sa.String(100), nullable=True),
        sa.Column("latest_run_finished_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("latest_run_stat_date", sa.Date(), nullable=True),

        # ── refresh timestamp ──
        sa.Column("refreshed_at", mysql.DATETIME(fsp=3), nullable=False),

        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )

    # Primary query patterns:
    #   1. cockpit overview: WHERE level_type IN ('BRANCH','GRID','CHANNEL')
    #   2. drill-down:       WHERE parent_id = ? AND level_type = ?
    #   3. branch list:      WHERE level_type = 'BRANCH'
    op.create_index("ix_wide_level_parent", "dashboard_wide", ["level_type", "parent_id"])
    op.create_index("ix_wide_area_indicator", "dashboard_wide", ["area_id", "indicator_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_wide_area_indicator", table_name="dashboard_wide")
    op.drop_index("ix_wide_level_parent", table_name="dashboard_wide")
    op.drop_table("dashboard_wide")
