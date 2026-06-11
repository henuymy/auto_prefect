"""create indicator

Revision ID: 20260610_0004
Revises: 20260610_0003
Create Date: 2026-06-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260610_0004"
down_revision: Union[str, Sequence[str], None] = "20260610_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    indicator = op.create_table(
        "indicator",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "code",
            sa.String(length=100, collation="utf8mb4_bin"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_indicator")),
        sa.UniqueConstraint("code", name=op.f("uq_indicator_code")),
    )
    op.create_index(
        "ix_indicator_enabled_sort_order",
        "indicator",
        ["enabled", "sort_order"],
        unique=False,
    )
    op.bulk_insert(
        indicator,
        [
            {
                "code": "sgs_ajvwdz",
                "name": "爱家亲情网(V网版)",
                "enabled": True,
                "sort_order": 10,
            }
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_indicator_enabled_sort_order", table_name="indicator")
    op.drop_table("indicator")
