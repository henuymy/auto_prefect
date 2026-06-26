"""add indicator type and storage mode

Revision ID: 20260626_0019
Revises: 20260626_0018
Create Date: 2026-06-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260626_0019"
down_revision: Union[str, Sequence[str], None] = "20260626_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "indicator",
        sa.Column(
            "indicator_type",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'SOURCE'"),
        ),
    )
    op.add_column(
        "indicator",
        sa.Column(
            "storage_mode",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'STORE'"),
        ),
    )
    op.create_index(
        "ix_indicator_enabled_storage_sort_order",
        "indicator",
        ["enabled", "storage_mode", "sort_order"],
    )


def downgrade() -> None:
    op.drop_index("ix_indicator_enabled_storage_sort_order", table_name="indicator")
    op.drop_column("indicator", "storage_mode")
    op.drop_column("indicator", "indicator_type")
