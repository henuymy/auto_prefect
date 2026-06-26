"""mark component owners as custom indicators

Revision ID: 20260626_0021
Revises: 20260626_0020
Create Date: 2026-06-26
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260626_0021"
down_revision: Union[str, Sequence[str], None] = "20260626_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE indicator i
        SET i.indicator_type = 'CUSTOM',
            i.source_active = 0,
            i.storage_mode = 'STORE'
        WHERE EXISTS (
            SELECT 1
            FROM custom_indicator_component c
            WHERE c.custom_indicator_id = i.id
        )
        """
    )


def downgrade() -> None:
    pass
