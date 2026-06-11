"""rename cumulative upsert count

Revision ID: 20260611_0010
Revises: 20260611_0009
Create Date: 2026-06-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260611_0010"
down_revision: Union[str, Sequence[str], None] = "20260611_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "collection_run",
        "daily_upsert_count",
        new_column_name="acc_upsert_count",
        existing_type=mysql.INTEGER(unsigned=True),
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )


def downgrade() -> None:
    op.alter_column(
        "collection_run",
        "acc_upsert_count",
        new_column_name="daily_upsert_count",
        existing_type=mysql.INTEGER(unsigned=True),
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )
