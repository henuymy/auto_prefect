"""create metric tables

Revision ID: 20260610_0005
Revises: 20260610_0004
Create Date: 2026-06-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260610_0005"
down_revision: Union[str, Sequence[str], None] = "20260610_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "metric_current",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("area_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("indicator_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("collection_run_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("metric_value", mysql.DECIMAL(20, 4), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("collected_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["area_id"],
            ["area.id"],
            name=op.f("fk_metric_current_area_id_area"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["collection_run_id"],
            ["collection_run.id"],
            name=op.f("fk_metric_current_collection_run_id_collection_run"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["indicator_id"],
            ["indicator.id"],
            name=op.f("fk_metric_current_indicator_id_indicator"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_current")),
        sa.UniqueConstraint(
            "area_id",
            "indicator_id",
            name="uq_metric_current_area_indicator",
        ),
    )
    op.create_index(
        "ix_metric_current_indicator_value",
        "metric_current",
        ["indicator_id", "metric_value"],
        unique=False,
    )
    op.create_index(
        "ix_metric_current_stat_date_indicator",
        "metric_current",
        ["stat_date", "indicator_id"],
        unique=False,
    )

    op.create_table(
        "metric_snapshot",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("collection_run_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("area_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("indicator_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("metric_value", mysql.DECIMAL(20, 4), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("collected_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["area_id"],
            ["area.id"],
            name=op.f("fk_metric_snapshot_area_id_area"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["collection_run_id"],
            ["collection_run.id"],
            name=op.f("fk_metric_snapshot_collection_run_id_collection_run"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["indicator_id"],
            ["indicator.id"],
            name=op.f("fk_metric_snapshot_indicator_id_indicator"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_snapshot")),
        sa.UniqueConstraint(
            "collection_run_id",
            "area_id",
            "indicator_id",
            name="uq_metric_snapshot_run_area_indicator",
        ),
    )
    op.create_index(
        "ix_metric_snapshot_area_indicator_collected",
        "metric_snapshot",
        ["area_id", "indicator_id", "collected_at"],
        unique=False,
    )
    op.create_index(
        "ix_metric_snapshot_stat_date_indicator",
        "metric_snapshot",
        ["stat_date", "indicator_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_metric_snapshot_stat_date_indicator",
        table_name="metric_snapshot",
    )
    op.drop_index(
        "ix_metric_snapshot_area_indicator_collected",
        table_name="metric_snapshot",
    )
    op.drop_table("metric_snapshot")
    op.drop_index(
        "ix_metric_current_stat_date_indicator",
        table_name="metric_current",
    )
    op.drop_index(
        "ix_metric_current_indicator_value",
        table_name="metric_current",
    )
    op.drop_table("metric_current")
