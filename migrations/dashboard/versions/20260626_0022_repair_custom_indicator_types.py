"""repair custom indicator types after component edits

Revision ID: 20260626_0022
Revises: 20260626_0021
Create Date: 2026-06-26
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260626_0022"
down_revision: Union[str, Sequence[str], None] = "20260626_0021"
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
