"""create channel manager to channel relationship table

Revision ID: 20260616_0013
Revises: 20260614_0012
Create Date: 2026-06-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260616_0013"
down_revision: Union[str, Sequence[str], None] = "20260614_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channel_manager_area",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("manager_target_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("channel_area_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("grid_area_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("enabled", mysql.BOOLEAN(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_seen_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column(
            "missing_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
            server_default=sa.text(
                "CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["manager_target_id"],
            ["request_target.id"],
            name="fk_channel_manager_area_manager_target_id_request_target",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["channel_area_id"],
            ["area.id"],
            name="fk_channel_manager_area_channel_area_id_area",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["grid_area_id"],
            ["area.id"],
            name="fk_channel_manager_area_grid_area_id_area",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "manager_target_id",
            "channel_area_id",
            name="uq_channel_manager_area_manager_channel",
        ),
        mysql_charset="utf8mb4",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_channel_manager_area_manager_enabled",
        "channel_manager_area",
        ["manager_target_id", "enabled"],
    )
    op.create_index(
        "ix_channel_manager_area_channel_enabled",
        "channel_manager_area",
        ["channel_area_id", "enabled"],
    )
    op.create_index(
        "ix_channel_manager_area_grid",
        "channel_manager_area",
        ["grid_area_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_channel_manager_area_grid", table_name="channel_manager_area")
    op.drop_index(
        "ix_channel_manager_area_channel_enabled",
        table_name="channel_manager_area",
    )
    op.drop_index(
        "ix_channel_manager_area_manager_enabled",
        table_name="channel_manager_area",
    )
    op.drop_table("channel_manager_area")
