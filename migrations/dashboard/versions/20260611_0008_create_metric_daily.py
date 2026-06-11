"""create daily cumulative metric storage

Revision ID: 20260611_0008
Revises: 20260611_0007
Create Date: 2026-06-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260611_0008"
down_revision: Union[str, Sequence[str], None] = "20260611_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "collection_run",
        sa.Column(
            "run_type",
            sa.String(length=16),
            server_default=sa.text("'REALTIME'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_collection_run_valid_run_type"),
        "collection_run",
        "run_type IN ('REALTIME', 'DAILY')",
    )
    op.add_column(
        "collection_run",
        sa.Column(
            "daily_upsert_count",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_table(
        "metric_daily",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("area_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("indicator_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("collection_run_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("metric_value", mysql.DECIMAL(20, 4), nullable=False),
        sa.Column("collected_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["area_id"],
            ["area.id"],
            name=op.f("fk_metric_daily_area_id_area"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["indicator_id"],
            ["indicator.id"],
            name=op.f("fk_metric_daily_indicator_id_indicator"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["collection_run_id"],
            ["collection_run.id"],
            name=op.f("fk_metric_daily_collection_run_id_collection_run"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_daily")),
        sa.UniqueConstraint(
            "stat_date",
            "area_id",
            "indicator_id",
            name="uq_metric_daily_date_area_indicator",
        ),
    )
    op.create_index(
        "ix_metric_daily_area_indicator_date",
        "metric_daily",
        ["area_id", "indicator_id", "stat_date"],
        unique=False,
    )
    op.create_index(
        "ix_metric_daily_date_indicator_value",
        "metric_daily",
        ["stat_date", "indicator_id", "metric_value"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_metric_daily_date_indicator_value",
        table_name="metric_daily",
    )
    op.drop_index(
        "ix_metric_daily_area_indicator_date",
        table_name="metric_daily",
    )
    op.drop_table("metric_daily")
    op.drop_constraint(
        op.f("ck_collection_run_valid_run_type"),
        "collection_run",
        type_="check",
    )
    op.drop_column("collection_run", "daily_upsert_count")
    op.drop_column("collection_run", "run_type")
