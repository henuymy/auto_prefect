"""add structure change summary to collection_run

Revision ID: 20260616_0014
Revises: 20260616_0013
Create Date: 2026-06-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260616_0014"
down_revision: Union[str, Sequence[str], None] = "20260616_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "collection_run",
        sa.Column("structure_change_summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("collection_run", "structure_change_summary")
