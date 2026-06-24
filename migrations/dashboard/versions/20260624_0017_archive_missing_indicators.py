"""archive indicators missing from the source catalog

Revision ID: 20260624_0017
Revises: 20260618_0016
Create Date: 2026-06-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260624_0017"
down_revision: Union[str, Sequence[str], None] = "20260618_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "indicator",
        sa.Column(
            "source_active",
            mysql.BOOLEAN(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "indicator",
        sa.Column("removed_at", mysql.DATETIME(fsp=3), nullable=True),
    )
    op.create_index(
        "ix_indicator_source_active_sort_order",
        "indicator",
        ["source_active", "sort_order"],
    )


def downgrade() -> None:
    op.drop_index("ix_indicator_source_active_sort_order", table_name="indicator")
    op.drop_column("indicator", "removed_at")
    op.drop_column("indicator", "source_active")
