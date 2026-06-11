"""create request target

Revision ID: 20260611_0007
Revises: 20260610_0006
Create Date: 2026-06-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260611_0007"
down_revision: Union[str, Sequence[str], None] = "20260610_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "request_target",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "target_code",
            sa.String(length=100, collation="utf8mb4_bin"),
            nullable=False,
        ),
        sa.Column("target_name", sa.String(length=200), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("area_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("parent_target_id", mysql.BIGINT(unsigned=True), nullable=True),
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
            "target_type IN ('CITY', 'BRANCH', 'GRID', 'CHANNEL_MANAGER')",
            name=op.f("ck_request_target_valid_target_type"),
        ),
        sa.ForeignKeyConstraint(
            ["area_id"],
            ["area.id"],
            name=op.f("fk_request_target_area_id_area"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_target_id"],
            ["request_target.id"],
            name=op.f("fk_request_target_parent_target_id_request_target"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_request_target")),
        sa.UniqueConstraint(
            "target_type",
            "target_code",
            name="uq_request_target_type_code",
        ),
    )
    op.create_index(
        "ix_request_target_enabled_type_order",
        "request_target",
        ["enabled", "target_type", "sort_order"],
        unique=False,
    )
    op.create_index(
        "ix_request_target_parent_id",
        "request_target",
        ["parent_target_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_request_target_parent_id", table_name="request_target")
    op.drop_index(
        "ix_request_target_enabled_type_order",
        table_name="request_target",
    )
    op.drop_table("request_target")
