"""create metric target table

Revision ID: 20260617_0015
Revises: 20260616_0014
Create Date: 2026-06-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260617_0015"
down_revision: Union[str, Sequence[str], None] = "20260616_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "metric_target",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True),
        sa.Column("period_type", sa.String(16), nullable=False),
        sa.Column("area_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("indicator_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("target_value", mysql.DECIMAL(20, 4), nullable=False),
        sa.Column("enabled", mysql.BOOLEAN(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.CheckConstraint(
            "period_type IN ('REALTIME', 'DAY_ACC', 'MONTH')",
            name="valid_metric_target_period_type",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_foreign_key(
        "fk_metric_target_area_id_area",
        "metric_target",
        "area",
        ["area_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_metric_target_indicator_id_indicator",
        "metric_target",
        "indicator",
        ["indicator_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_metric_target_period_area_indicator",
        "metric_target",
        ["period_type", "area_id", "indicator_id"],
    )
    op.create_index(
        "ix_metric_target_area_indicator_period_enabled",
        "metric_target",
        ["area_id", "indicator_id", "period_type", "enabled"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_metric_target_area_indicator_period_enabled",
        table_name="metric_target",
    )
    op.drop_constraint(
        "uq_metric_target_period_area_indicator",
        "metric_target",
        type_="unique",
    )
    op.drop_constraint(
        "fk_metric_target_indicator_id_indicator",
        "metric_target",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_metric_target_area_id_area",
        "metric_target",
        type_="foreignkey",
    )
    op.drop_table("metric_target")
