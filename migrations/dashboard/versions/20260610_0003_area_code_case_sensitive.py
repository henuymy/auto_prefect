"""make area code case sensitive

Revision ID: 20260610_0003
Revises: 20260610_0002
Create Date: 2026-06-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260610_0003"
down_revision: Union[str, Sequence[str], None] = "20260610_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "area",
        "area_code",
        existing_type=sa.String(length=100),
        type_=sa.String(length=100, collation="utf8mb4_bin"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "area",
        "area_code",
        existing_type=sa.String(length=100, collation="utf8mb4_bin"),
        type_=sa.String(length=100, collation="utf8mb4_unicode_ci"),
        existing_nullable=False,
    )
