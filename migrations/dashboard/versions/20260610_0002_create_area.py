"""create area

Revision ID: 20260610_0002
Revises: 20260610_0001
Create Date: 2026-06-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260610_0002"
down_revision: Union[str, Sequence[str], None] = "20260610_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "area",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("area_code", sa.String(length=100), nullable=False),
        sa.Column("area_name", sa.String(length=200), nullable=False),
        sa.Column("level_type", sa.String(length=20), nullable=False),
        sa.Column("level_no", mysql.TINYINT(unsigned=True), nullable=False),
        sa.Column("parent_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column(
            "enabled",
            mysql.BOOLEAN(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "sort_order",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_seen_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column(
            "missing_count",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "level_type IN ('CITY', 'BRANCH', 'GRID', 'CHANNEL')",
            name=op.f("ck_area_valid_level_type"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["area.id"],
            name=op.f("fk_area_parent_id_area"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_area")),
        sa.UniqueConstraint(
            "level_type",
            "area_code",
            name="uq_area_level_type_area_code",
        ),
    )
    op.create_index(
        "ix_area_level_type_enabled",
        "area",
        ["level_type", "enabled"],
        unique=False,
    )
    op.create_index(
        "ix_area_parent_id_enabled",
        "area",
        ["parent_id", "enabled"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_area_parent_id_enabled", table_name="area")
    op.drop_index("ix_area_level_type_enabled", table_name="area")
    op.drop_table("area")
